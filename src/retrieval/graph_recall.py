"""
graph_recall.py — 图谱检索召回（把图谱从「可视化」升级成「检索导航」）

设计原则（对齐"伏羲"统一/有序/稳定）：
  - 统一   ：作为 search.py 的第三个召回源，返回与 BM25/向量完全一致的结构
             [{id, content, file_name, file_id, chunk_index, score, source}]
             复用现有 weighted_rrf_fusion 融合 + rerank 精排，不另起炉灶
  - 有序   ：query 实体识别 → 命中实体 → 沿实体反查 chunk → 打分排序
  - 稳定   ：纯规则 + SQL 反查，零 LLM、零网络，任何异常降级返回空列表不阻断

召回路径：
  1. 用实体词典/正则从 query 里识别「硬实体」（标准号/型号/材料）—— 精确导航
  2. 命中实体 → get_entity_chunks 沿 entity_chunks 反查被提到的 chunk
  3. 命中的 chunk 作为图谱召回结果，score 由 mention_count + 实体精确度决定
  4. 可选扩展：沿实体关系（spec/uses_standard/compatible_process）一跳邻居关联 chunk

Feature Flag：RAG_GRAPH_RECALL=1（默认开启，置 0 关闭图谱召回）
"""
import logging
import re

from src.storage import db
from src.extraction import entity_extractor
from config import RAG_GRAPH_RECALL

logger = logging.getLogger("rag.retrieval.graph")


def _use_graph_recall() -> bool:
    return RAG_GRAPH_RECALL == "1"


# ============================================================
# query 实体识别（纯规则，零 LLM，稳定可预测）
# ============================================================

def detect_query_entities(query: str) -> list[dict]:
    """从 query 里识别硬实体，返回 [{name, type}]（排重后）

    复用 entity_extractor 的抽取规则，只保留「精确可导航」的实体类型：
    standard / connector / material —— 这三类有明确的词典/正则，能稳定命中。
    process / param 兜底也纳入（参数名可导航到相关 chunk）。
    """
    if not query:
        return []
    raw = entity_extractor.extract_rule(query)
    # 只保留可导航类型，排除泛化描述
    nav_types = {"standard", "connector", "material", "param", "process"}
    out = []
    seen = set()
    for e in raw:
        key = (e["name"].lower(), e["type"])
        if e["type"] not in nav_types:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": e["name"], "type": e["type"]})
    return out


# ============================================================
# 实体 → chunk 反查
# ============================================================

def _entity_to_chunks(entity: dict, limit: int = 20) -> list[dict]:
    """命中实体 → 反查被它提到的 chunk（含精确位置锚点）"""
    ent = db.get_entity_by_name(entity["name"], entity["type"])
    if not ent:
        # 尝试不加 type 兜底（标准号规范化后 type 可能不一致）
        ent = db.get_entity_by_name(entity["name"])
    if not ent:
        return []
    chunks = db.get_entity_chunks(ent["id"])
    results = []
    for c in chunks[:limit]:
        results.append({
            "id": c["chunk_id"],
            "content": c["content"],
            "file_id": c["file_id"],
            "chunk_index": c.get("chunk_index"),
            "file_name": c["file_name"],
            "score": float(c.get("mention_count", 1)),
            "source": "graph",
            "_graph_entity": entity["name"],
            "_graph_etype": entity["type"],
        })
    return results


def _neighbor_chunks(entity: dict, limit: int = 20) -> list[dict]:
    """一跳邻居扩展：沿强语义关系（spec/uses_standard/compatible_process）找相邻实体，
    再反查相邻实体被提到的 chunk（用于「GB/T 3077 → 相关材料/工艺」这类关联导航）

    只走强语义边，不走弱 cooccur 边，避免把无关共现拉到结果里。
    """
    ent = db.get_entity_by_name(entity["name"], entity["type"]) or db.get_entity_by_name(entity["name"])
    if not ent:
        return []
    # 只扩展强语义关系
    neighbors = db.get_entity_relations(ent["id"], rel_types=["spec", "uses_standard", "compatible_process"])
    results = []
    seen_chunk = set()
    for nb in neighbors[:30]:
        # 邻居实体 id（关系另一端）
        nb_id = nb["target_id"] if nb["source_id"] == ent["id"] else nb["source_id"]
        chunks = db.get_entity_chunks(nb_id)
        for c in chunks:
            cid = c["chunk_id"]
            if cid in seen_chunk:
                continue
            seen_chunk.add(cid)
            results.append({
                "id": cid,
                "content": c["content"],
                "file_id": c["file_id"],
                "chunk_index": c.get("chunk_index"),
                "file_name": c["file_name"],
                "score": 0.6 * float(c.get("mention_count", 1)),  # 邻居权重略低
                "source": "graph_neighbor",
                "_graph_entity": entity["name"],
                "_graph_etype": entity["type"],
            })
            if len(results) >= limit:
                return results
    return results


# ============================================================
# 入口：图谱召回
# ============================================================

def graph_recall(query: str, limit: int = 20, expand_neighbor: bool = True) -> list[dict]:
    """图谱召回：识别 query 实体 → 反查 chunk（+ 一跳邻居）

    返回与 BM25/向量一致结构的 chunk 列表，失败返回空列表（降级不阻断）
    """
    if not _use_graph_recall():
        return []
    try:
        entities = detect_query_entities(query)
        if not entities:
            return []

        results = []
        seen_chunk = set()
        # 硬实体（标准/型号/材料）权重高，先召回
        weighted = sorted(entities, key=lambda e: _entity_type_weight(e["type"]), reverse=True)
        for entity in weighted:
            ents = _entity_to_chunks(entity, limit=10)
            for r in ents:
                cid = r["id"]
                if cid in seen_chunk:
                    continue
                seen_chunk.add(cid)
                # 实体类型权重乘到 score 上（标准号/型号精确导航 > 泛化材料）
                r["score"] = r["score"] * _entity_type_weight(entity["type"])
                results.append(r)

        # 一跳邻居扩展（可选）
        if expand_neighbor:
            for entity in weighted[:3]:  # 只对 top3 硬实体做邻居扩展，控制规模
                for r in _neighbor_chunks(entity, limit=8):
                    cid = r["id"]
                    if cid in seen_chunk:
                        continue
                    seen_chunk.add(cid)
                    results.append(r)

        # 按 score 降序截断
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        if results:
            logger.info(f"图谱召回: query 命中 {len(entities)} 实体, 返回 {len(results)} chunks")
        return results[:limit]
    except Exception as e:
        logger.warning(f"图谱召回失败（降级返回空）: {e}")
        return []


def _entity_type_weight(etype: str) -> float:
    """实体类型导航权重：精确可定位的类型权重高"""
    return {
        "standard": 1.0,     # 标准号最精确（GB/T 3077 直接定位）
        "connector": 0.9,    # 型号精确
        "material": 0.7,     # 材料中等
        "param": 0.8,        # 参数名较精确
        "process": 0.5,      # 工艺相对宽泛
    }.get(etype, 0.5)
