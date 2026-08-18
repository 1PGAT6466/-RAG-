"""
插件管理器 — 最小闭环统一入口

流程：扫描 plugins/ 目录 → 安装/更新 registry → 通过 Host 调用
"""
import json
import logging
from pathlib import Path

from config import BASE_DIR
from src.plugins import registry, lifecycle, host

logger = logging.getLogger("rag.plugins")

PLUGINS_DIR = BASE_DIR / "plugins"

# 主框架插件 API 版本（manifest.api_version 必须匹配）
API_VERSION = "1"


def init():
    """初始化插件系统：建库 + 扫描本地目录 + 安装"""
    registry.init_db()
    _scan_and_install()
    logger.info(f"插件系统就绪，API 版本 {API_VERSION}")


def _scan_and_install() -> list[str]:
    """扫描 plugins/ 下每个子目录的 manifest.json 并安装"""
    if not PLUGINS_DIR.exists():
        return []
    installed = []
    for subdir in PLUGINS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        manifest_path = subdir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # api_version 严格的契约校验
            mv = manifest.get("api_version", "")
            if mv != API_VERSION:
                logger.warning(
                    f"插件 {manifest.get('name','?')} api_version={mv!r} 与框架 {API_VERSION!r} 不匹配，拒绝加载"
                )
                continue
            lifecycle.install(manifest, subdir)
            installed.append(manifest.get("name", ""))
        except Exception as e:
            logger.warning(f"插件加载失败 {subdir.name}: {e}")
    return installed


def list_plugins() -> list[dict]:
    """返回已加载插件列表（含 manifest、状态）"""
    result = []
    for row in registry.list_all():
        d = dict(row)
        d["manifest"] = registry.get_manifest(d["name"])
        result.append(d)
    return result


def get_enabled_tools() -> list[dict]:
    """返回所有已启用 Tool 型插件的工具清单（供 Agent function-call 用）"""
    tools = []
    for row in registry.list_all():
        if row["status"] != "enabled" or row["kind"] != "tool":
            continue
        manifest = registry.get_manifest(row["name"])
        if not manifest:
            continue
        for t in manifest.get("tools", []):
            tools.append({
                "plugin": row["name"],
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters_schema": t.get("parameters_schema", {}),
            })
    return tools


def invoke(name: str, method: str, params: dict = None) -> dict:
    """调用插件（Tool 型的方法，或 Workflow 型的入口）"""
    row = registry.get(name)
    if not row:
        return {"error": f"插件不存在: {name}"}
    if row["status"] != "enabled":
        return {"error": f"插件未启用: {name}（当前状态 {row['status']}）"}

    manifest = registry.get_manifest(name) or {}

    # 方法白名单：只允许调用 manifest 声明的 tool 名（或 hook 方法名），
    # 防任意登录用户调用插件内部的辅助函数/未声明方法（最小权限）
    allowed_methods = set()
    for t in manifest.get("tools", []):
        if t.get("name"):
            allowed_methods.add(t["name"])
    for hdef in (manifest.get("hooks") or {}).values():
        if isinstance(hdef, dict) and hdef.get("method"):
            allowed_methods.add(hdef["method"])
    # kind=workflow 且无 tools/hooks 时，允许 manifest 声明的 entry 方法名兜底
    if manifest.get("kind") == "workflow" and not allowed_methods:
        allowed_methods.add(manifest.get("entry", "main.py").replace(".py", ""))
    if allowed_methods and method not in allowed_methods:
        return {"error": f"方法 {method!r} 未在插件 manifest 中声明（白名单: {sorted(allowed_methods)}）"}

    entry = manifest.get("entry", "main.py")
    entry_path = PLUGINS_DIR / name / entry
    if not entry_path.exists():
        return {"error": f"插件入口文件缺失: {entry_path}"}

    pool = host.get_pool()
    return pool.invoke(name, str(entry_path), method, params)
