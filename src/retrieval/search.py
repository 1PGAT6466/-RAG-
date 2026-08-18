"""
检索引擎 — BM25 + 向量混合检索 + 结果融合
"""
import logging
import heapq
from config import SEARCH_TOP_K, BM25_WEIGHT, VECTOR_WEIGHT, RAG_DYNAMIC_RANKING, RAG_RERANK
from src.storage.db import fts_search
from src.pipeline.embedder import encode_query, cosine_similarity

logger = logging.getLogger("rag.retrieval")


def _use_dynamic_ranking() -> bool:
    """是否启用动态融合权重（Feature Flag，默认关闭可回退原硬编码权重）"""
    return RAG_DYNAMIC_RANKING == "1"


def _use_rerank() -> bool:
    """是否启用 Rerank 精排（Feature Flag，默认开启，检索后精排）"""
    return RAG_RERANK == "1"


async def search(query: str, top_k: int = None, with_rerank: bool = True) -> list[dict]:
    """
    混合检索：BM25 + 向量 → 动态加权 RRF 融合 → 精确/分类加权 → Rerank 精排
    返回 [{id, content, file_name, file_id, score, source}, ...]
    """
    if top_k is None:
        top_k = SEARCH_TOP_K
    k = max(top_k, 20)

    # 1. BM25 召回 top-30
    bm25_results = _bm25_search(query, limit=30)

    # 2. 向量检索
    vec_results = _vector_search(query, limit=30)

    # 3. 图谱召回（实体导航，第三个召回源）
    graph_results = _graph_recall(query, limit=30)

    # 4. 融合（动态 α 或原硬编码权重）
    if _use_dynamic_ranking():
        from src.retrieval.ranking import weighted_rrf_fusion, post_rank
        merged = weighted_rrf_fusion(bm25_results, vec_results, query, top_k, graph=graph_results)
        merged = post_rank(query, merged)
    else:
        merged = _rrf_fusion(bm25_results, vec_results, top_k)

    # 4. Rerank 精排（可选）
    if with_rerank and _use_rerank() and merged:
        try:
            from src.retrieval.rerank import rerank
            merged = await rerank(query, merged, top_k=top_k)
        except Exception as e:
            logger.warning(f"Rerank 失败，使用融合结果: {e}")

    logger.info(f"检索完成: query='{query[:50]}', results={len(merged)}")

    # 5. Workflow hook：检索后处理（声明式插件，容错不阻断）
    try:
        from src.plugins.hooks import run_hook_async
        hook_result = await run_hook_async("on_search", {
            "query": query,
            "results": merged,
            "top_k": top_k,
        })
        # hook 若返回了新的 results，则采用（否则保持原结果）
        if hook_result.get("ok"):
            altered = hook_result["payload"].get("results")
            if isinstance(altered, list):
                merged = altered
    except Exception as e:
        logger.warning(f"on_search hook 异常（已忽略）: {e}")

    return merged


def _graph_recall(query: str, limit: int = 30) -> list[dict]:
    """图谱召回（实体导航），失败降级空列表"""
    try:
        from src.retrieval.graph_recall import graph_recall
        return graph_recall(query, limit=limit)
    except Exception as e:
        logger.warning(f"图谱召回失败: {e}")
        return []


def _bm25_search(query: str, limit: int = 30) -> list[dict]:
    """FTS5 BM25 搜索"""
    try:
        return fts_search(query, limit=limit)
    except Exception as e:
        logger.warning(f"BM25 搜索失败: {e}")
        return []


def _vector_search(query: str, limit: int = 30) -> list[dict]:
    """向量相似度搜索（ChromaDB，回退 SQLite 暴力扫描）"""
    try:
        q_vec = encode_query(query)

        from src.storage.chroma_store import _use_chroma
        if _use_chroma():
            from src.storage.chroma_store import search as chroma_search
            results = chroma_search(q_vec, top_k=limit)
            # 补齐 file_name
            return _attach_file_names(results)
        return _vector_search_sqlite(q_vec, limit)
    except Exception as e:
        logger.warning(f"向量搜索失败: {e}")
        return []


def _attach_file_names(results: list[dict]) -> list[dict]:
    """为结果补充 file_name（Chroma 只存了 file_id）
    批量查询文件映射，避免每个结果一次 DB 查询（N+1）。
    """
    if not results:
        return results
    from src.storage.db import _get_conn
    conn = _get_conn()
    file_ids = {r["file_id"] for r in results}
    placeholders = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"SELECT id, name FROM files WHERE id IN ({placeholders})",
        tuple(file_ids),
    ).fetchall()
    name_map = {r["id"]: r["name"] for r in rows}
    for r in results:
        r["file_name"] = name_map.get(r["file_id"], "")
    return results


def _vector_search_sqlite(q_vec: bytes, limit: int = 30) -> list[dict]:
    """SQLite 暴力扫描（回退路径，数据量小时可用）"""
    from src.storage.db import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT c.id, c.content, c.file_id, c.chunk_index, c.embedding, f.name as file_name "
        "FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.embedding IS NOT NULL"
    ).fetchall()

    from src.pipeline.embedder import cosine_similarity
    results = []
    for row in rows:
        if row["embedding"]:
            sim = cosine_similarity(q_vec, row["embedding"])
            if sim > 0.3:  # 阈值过滤
                results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "file_id": row["file_id"],
                    "chunk_index": row["chunk_index"],
                    "file_name": row["file_name"],
                    "score": float(sim),
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def _rrf_fusion(bm25: list[dict], vec: list[dict], top_k: int, k: int = 60) -> list[dict]:
    """RRF 融合 + 去重"""
    scores = {}
    details = {}

    # BM25 排名
    for rank, item in enumerate(bm25):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + BM25_WEIGHT / (k + rank + 1)
        details[cid] = item

    # 向量排名
    for rank, item in enumerate(vec):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + VECTOR_WEIGHT / (k + rank + 1)
        if cid not in details:
            details[cid] = item

    # 排序取 top_k
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for cid, score in ranked:
        item = details[cid]
        item["score"] = score
        item["source"] = "bm25+vector"
        result.append(item)

    return result
