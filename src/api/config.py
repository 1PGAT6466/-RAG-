"""
api/config.py — 系统配置管理（D 方案：轻量配置面板）

设计（避免臃肿）：
  - 只暴露「运行参量 + 功能开关」两类可热调配置，不碰基础设施（密钥/路径/端口）。
  - 只读 + 改值两个端点，不做历史版本/回滚/审批流（过度设计）。
  - 改值写回 .env（重启生效），不做运行态热更（保持简单、可靠、可追溯）。

可管理配置清单由 _CONFIG_ITEMS 显式声明（白名单），不在清单里的 .env 项
不暴露到面板，避免误改密钥/路径等部署配置。
"""
import os
import re
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from config import BASE_DIR
from src.auth.deps import require_admin

logger = logging.getLogger("rag.api.config")
router = APIRouter()

ENV_PATH = BASE_DIR / ".env"

# 可管理配置白名单：{env_key: {label, type, group, description, attr}}
# type: "int" | "float" | "string" | "bool"（bool 存 "1"/"0"）
# group: "检索" | "入库" | "LLM" | "缓存" | "开关"
# attr：config 模块中对应的属性名（用于读取「当前运行值」，区分运行值 vs .env 值）
_CONFIG_ITEMS = {
    # 检索运行参量
    "SEARCH_TOP_K":       {"label": "返回结果数",       "type": "int",   "group": "检索", "desc": "检索返回的 top 文档数", "attr": "SEARCH_TOP_K"},
    "BM25_WEIGHT":        {"label": "BM25 权重",        "type": "float", "group": "检索", "desc": "混合检索中 BM25 权重（与向量权重配合）", "attr": "BM25_WEIGHT"},
    "VECTOR_WEIGHT":      {"label": "向量权重",         "type": "float", "group": "检索", "desc": "混合检索中向量权重", "attr": "VECTOR_WEIGHT"},
    "BM25_RECALL_LIMIT":  {"label": "BM25 召回数",      "type": "int",   "group": "检索", "desc": "BM25 召回候选池大小", "attr": "BM25_RECALL_LIMIT"},
    "BM25_PER_FILE":      {"label": "每文件保留 chunk", "type": "int",   "group": "检索", "desc": "BM25 每文件最多保留的 chunk 数（防大文档碾压）", "attr": "BM25_PER_FILE"},
    "VECTOR_RECALL_LIMIT": {"label": "向量召回数",       "type": "int",   "group": "检索", "desc": "向量召回候选数", "attr": "VECTOR_RECALL_LIMIT"},
    "GRAPH_RECALL_LIMIT": {"label": "图谱召回数",       "type": "int",   "group": "检索", "desc": "图谱召回候选数", "attr": "GRAPH_RECALL_LIMIT"},
    "VECTOR_HIGH_CONF_THRESHOLD": {"label": "向量高置信阈值", "type": "float", "group": "检索", "desc": "无词汇命中时保留高置信向量结果的阈值", "attr": "VECTOR_HIGH_CONF_THRESHOLD"},
    "RRF_K":              {"label": "RRF 常数 k",       "type": "int",   "group": "检索", "desc": "RRF 融合常数", "attr": "RRF_K"},
    # 语义缓存
    "SEMANTIC_CACHE_THRESHOLD": {"label": "语义缓存阈值", "type": "float", "group": "缓存", "desc": "语义缓存余弦相似度命中阈值", "attr": "SEMANTIC_CACHE_THRESHOLD"},
    "SEMANTIC_CACHE_MAX": {"label": "语义缓存上限",     "type": "int",   "group": "缓存", "desc": "语义缓存最大条目数", "attr": "SEMANTIC_CACHE_MAX"},
    # LLM 超时
    "DEEPSEEK_TIMEOUT":   {"label": "LLM 超时(秒)",     "type": "int",   "group": "LLM", "desc": "DeepSeek 调用超时秒数", "attr": "DEEPSEEK_TIMEOUT"},
    # 功能开关（布尔，存 1/0）
    "RAG_CHROMA":         {"label": "ChromaDB 向量存储", "type": "bool", "group": "开关", "desc": "用 ChromaDB 替代 SQLite 暴力扫描", "attr": "RAG_CHROMA"},
    "RAG_JIEBA":          {"label": "jieba 分词",        "type": "bool", "group": "开关", "desc": "FTS5 使用 jieba 分词", "attr": "RAG_JIEBA"},
    "RAG_DYNAMIC_RANKING": {"label": "动态融合权重",      "type": "bool", "group": "开关", "desc": "按查询类型动态分配 BM25/向量权重", "attr": "RAG_DYNAMIC_RANKING"},
    "RAG_RERANK":         {"label": "Rerank 精排",       "type": "bool", "group": "开关", "desc": "检索后 Rerank 精排", "attr": "RAG_RERANK"},
    "RAG_GRAPH_RECALL":   {"label": "图谱召回",          "type": "bool", "group": "开关", "desc": "实体导航作为第三个召回源", "attr": "RAG_GRAPH_RECALL"},
    "RAG_ENTITY_EXTRACT": {"label": "实体抽取",          "type": "bool", "group": "开关", "desc": "规则实体抽取", "attr": "RAG_ENTITY_EXTRACT"},
    "RAG_HYDE":           {"label": "HyDE 兜底",         "type": "bool", "group": "开关", "desc": "语义模糊零召回时 LLM 假设答案兜底（默认关）", "attr": "RAG_HYDE"},
    "RAG_LANG_FILTER":    {"label": "繁简归一化",        "type": "bool", "group": "开关", "desc": "繁体→简体 + 非中文过滤", "attr": "RAG_LANG_FILTER"},
}


def _read_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _read_env_map() -> dict:
    """读取 .env 当前键值（跳过注释/空行）"""
    m = {}
    for line in _read_env_lines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        m[k.strip()] = v.strip()
    return m


@router.get("/api/config")
def get_config(_=Depends(require_admin)):
    """返回可管理配置清单（含当前 .env 值、运行值、类型、分组、说明）。

    运行值（runtime_value）是进程启动时加载的值；.env 值（value）是当前磁盘上的值。
    若用户改过 .env 但未重启，两者会不一致——前端据此提示「待重启生效」。
    """
    env_map = _read_env_map()
    runtime_map = _read_runtime_map()
    items = []
    for key, meta in _CONFIG_ITEMS.items():
        cur = env_map.get(key, "")
        runtime = runtime_map.get(key, "")
        # .env 未显式配置时，value 回退到运行值（即默认值），避免面板显示空
        if not cur and runtime:
            cur = runtime
        # bool 显示为开关语义
        value = cur
        if meta["type"] == "bool":
            value = "1" if cur == "1" else "0"
        items.append({
            "key": key,
            "label": meta["label"],
            "type": meta["type"],
            "group": meta["group"],
            "desc": meta["desc"],
            "value": value,
            "runtime_value": runtime,
            "pending_restart": bool(runtime and cur and str(runtime).strip() != str(cur).strip()),
            "default": cur if cur else _default_for(meta["type"]),
        })
    # 按 group 分组
    groups = {}
    for it in items:
        groups.setdefault(it["group"], []).append(it)
    return {"status": "ok", "groups": groups}


def _read_runtime_map() -> dict:
    """读取 config 模块当前加载的运行值（进程启动时从 .env 读入）"""
    try:
        import config as cfg
        out = {}
        for key, meta in _CONFIG_ITEMS.items():
            attr = meta.get("attr") or key
            val = getattr(cfg, attr, None)
            if val is None:
                continue
            if meta["type"] == "bool":
                out[key] = "1" if str(val) == "1" else "0"
            else:
                out[key] = str(val)
        return out
    except Exception:
        return {}


def _default_for(t: str) -> str:
    return {"int": "0", "float": "0.0", "bool": "0", "string": ""}.get(t, "")


@router.put("/api/config")
def update_config(body: dict, _=Depends(require_admin)):
    """更新配置项（仅白名单内的 key），写回 .env（重启生效）。"""
    key = (body or {}).get("key")
    value = (body or {}).get("value")
    if not key or key not in _CONFIG_ITEMS:
        raise HTTPException(status_code=400, detail="配置项不在可管理白名单内")
    meta = _CONFIG_ITEMS[key]
    value = str(value).strip()

    # 类型校验
    if meta["type"] == "bool":
        if value not in ("0", "1", "true", "false"):
            raise HTTPException(status_code=400, detail="布尔配置只能是 0/1 或 true/false")
        value = "1" if value in ("1", "true") else "0"
    elif meta["type"] == "int":
        try:
            int(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="需为整数")
    elif meta["type"] == "float":
        try:
            float(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="需为数字")

    # 写回 .env（保留原注释/顺序，仅替换或追加该 key）
    lines = _read_env_lines()
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info(f"配置更新: {key}={value}（重启后生效）")
    return {"status": "ok", "key": key, "value": value,
            "note": "已写入 .env，重启服务后生效"}
