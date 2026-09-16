"""
chroma_store.py — ChromaDB 向量存储（替换 SQLite 暴力向量扫描）

Feature Flag: RAG_CHROMA（默认 1，置 0 回退 SQLite 暴力扫描）

持久化目录: data/chroma
collection: chunks
存储: chunk_id(str) → embedding(1024 或 768 维) + content + file_id
"""
import logging
import os
import numpy as np

from config import DATA_DIR, RAG_CHROMA

logger = logging.getLogger("rag.chroma")

_CHROMA_DIR = str(DATA_DIR / "chroma")
_COLLECTION = "chunks"

_client = None
_collection = None


def _use_chroma() -> bool:
    return RAG_CHROMA == "1"


def _get_collection():
    global _client, _collection
    if _collection is None:
        import chromadb
        # 关闭 Chroma telemetry 噪音日志
        import chromadb.config as ccfg
        _client = chromadb.PersistentClient(
            path=_CHROMA_DIR,
            settings=ccfg.Settings(anonymized_telemetry=False),
        )
        # 关键：hnsw:sync_threshold 设超大值，禁用 hnsw 原生化持久化。
        # 根因：Chroma 0.6.3 在 Windows 下，达到 sync_threshold 触发 _persist 时，
        #        hnswlib 写出的 index.bin 损坏（缺失），重启后 load_index 报
        #        "Cannot open header file"。改为靠 chroma.sqlite3 持久化，重启时从 sqlite
        #        重建内存 HNSW（数据量小，重建开销可忽略）。
        _collection = _client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine", "hnsw:sync_threshold": 1000000000},
        )
        logger.info(f"ChromaDB 就绪: {_CHROMA_DIR}, collection={_COLLECTION}")
    return _collection


def add(chunk_id: int, embedding: bytes, content: str,
        file_id: int, chunk_index: int = 0) -> None:
    """写入/更新一条向量。

    content 不再存入 Chroma metadata（避免 [:500] 截断导致长 chunk 精确命中/rerank 失效）。
    检索后由 search._attach_file_names 从 chunks 主表按 id 回填完整 content。
    见 MEMORY 检索/召回缺陷9。
    """
    if not _use_chroma():
        return
    col = _get_collection()
    vec = _unpack(embedding)
    col.upsert(
        ids=[str(chunk_id)],
        embeddings=[vec.tolist()],
        metadatas=[{"file_id": file_id, "chunk_index": chunk_index}],
    )


# Chroma HNSW 单次 upsert 的批次上限（Chroma 0.6.x 实测 max_batch_size 为 5461）。
# 超过会抛 "Batch size N exceeds maximum batch size"，导致整批写入失败。
# 用保守阈值分批，避免 ensure_synced / 入库 Stage 大批量补齐时一次超限全丢。
_CHROMA_BATCH_LIMIT = 4000


def add_batch(rows: list[tuple]) -> None:
    """批量写入 [(chunk_id, embedding_bytes, content, file_id, chunk_index), ...]

    内部按 _CHROMA_BATCH_LIMIT 分批 upsert，规避 Chroma HNSW max_batch_size 超限。
    """
    if not _use_chroma():
        return
    if not rows:
        return
    col = _get_collection()
    for start in range(0, len(rows), _CHROMA_BATCH_LIMIT):
        batch = rows[start:start + _CHROMA_BATCH_LIMIT]
        ids = [str(r[0]) for r in batch]
        vecs = [_unpack(r[1]).tolist() for r in batch]
        metas = [{"file_id": r[3], "chunk_index": r[4]} for r in batch]
        col.upsert(ids=ids, embeddings=vecs, metadatas=metas)


def search(query_embedding: bytes, top_k: int = 30) -> list[dict]:
    """按 query 向量检索，返回 [{id, content, file_id, score, ...}]

    注：不在此处按相似度阈值过滤——实测 bge-large 对英文/字母串（如纯乱码
    'zzzzqqqq'）也会给出 0.5+ 的余弦相似度，与真实中文查询（0.54~0.69）重叠，
    阈值无法可靠区分。真正的相关性判据交给 BM25/FTS 关键词命中（见 search.py）。
    """
    col = _get_collection()
    qvec = _unpack(query_embedding).tolist()
    res = col.query(
        query_embeddings=[qvec],
        n_results=top_k,
        include=["metadatas", "distances"],
    )
    ids = res.get("ids", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    results = []
    for i, cid in enumerate(ids):
        meta = metas[i] if metas and i < len(metas) else {}
        dist = dists[i] if dists and i < len(dists) else 0.0
        # cosine 距离 → 相似度（cosine space 下 distance = 1 - cos_sim）
        score = max(0.0, 1.0 - float(dist))
        results.append({
            "id": int(cid),
            "content": meta.get("content", ""),
            "file_id": meta.get("file_id"),
            "chunk_index": meta.get("chunk_index", 0),
            "score": score,
        })
    return results


def delete(chunk_id: int) -> None:
    """删除一条向量"""
    if not _use_chroma():
        return
    col = _get_collection()
    col.delete(ids=[str(chunk_id)])


def delete_where(ids: list[str]) -> int:
    """批量删除向量（一次调用，避免逐条 delete 触发 HNSW 重建卡死）

    返回删除条数（Chroma delete 无返回值，传 id 不存在也不报错）。
    ids 为空时直接返回 0（Chroma 对空 ids 会报错）。
    """
    if not _use_chroma():
        return 0
    ids = [str(i) for i in ids if str(i)]
    if not ids:
        return 0
    col = _get_collection()
    # 分批，避免超大列表一次 upsert/delete 超 HNSW 批上限（虽 delete 无严格上限，保守分批）
    total = 0
    for start in range(0, len(ids), _CHROMA_BATCH_LIMIT):
        batch = ids[start:start + _CHROMA_BATCH_LIMIT]
        col.delete(ids=batch)
        total += len(batch)
    return total


def reset() -> None:
    """清空 collection（重建索引用）"""
    global _client, _collection
    if _client is not None:
        try:
            _client.delete_collection(_COLLECTION)
        except Exception:
            pass
        _collection = None
        _client = None


def ensure_synced() -> int:
    """启动时增量补齐：把 SQLite 里存在但 Chroma 缺失的向量补录。

    在常驻服务进程内调用，避免跨进程 HNSW 未落盘问题。
    返回补录条数。
    """
    if not _use_chroma():
        return 0
    try:
        col = _get_collection()
        # 已存 chunk id 集合
        existing = set(int(i) for i in col.get(include=[])['ids'])
    except Exception as e:
        logger.warning(f"读取 Chroma 现有 id 失败: {e}")
        existing = set()

    from src.storage.connection import _get_conn
    conn = _get_conn()
    # 分批加载，避免全量 chunks + embedding 一次性进内存（大库数百 MB）
    total_synced = 0
    batch_size = 1000
    offset = 0
    while True:
        rows = conn.execute(
            "SELECT id, file_id, chunk_index, content, embedding FROM chunks "
            "WHERE embedding IS NOT NULL LIMIT ? OFFSET ?",
            (batch_size, offset)
        ).fetchall()
        if not rows:
            break
        to_add = [r for r in rows if r['id'] not in existing]
        if to_add:
            add_batch([(r['id'], r['embedding'], r['content'], r['file_id'], r['chunk_index'])
                       for r in to_add])
            total_synced += len(to_add)
        offset += batch_size
    if total_synced:
        logger.info(f"Chroma 补齐 {total_synced} 条（总数 {count()}）")
    return total_synced


def count() -> int:
    """返回已存向量数"""
    if not _use_chroma():
        return 0
    col = _get_collection()
    return col.count()


def _unpack(data: bytes) -> np.ndarray:
    import struct
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)
