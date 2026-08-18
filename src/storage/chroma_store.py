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
    """写入/更新一条向量"""
    if not _use_chroma():
        return
    col = _get_collection()
    vec = _unpack(embedding)
    col.upsert(
        ids=[str(chunk_id)],
        embeddings=[vec.tolist()],
        metadatas=[{"content": content[:500], "file_id": file_id,
                    "chunk_index": chunk_index}],
    )


def add_batch(rows: list[tuple]) -> None:
    """批量写入 [(chunk_id, embedding_bytes, content, file_id, chunk_index), ...]"""
    if not _use_chroma():
        return
    if not rows:
        return
    col = _get_collection()
    ids = [str(r[0]) for r in rows]
    vecs = [_unpack(r[1]).tolist() for r in rows]
    metas = [{"content": (r[2] or "")[:500], "file_id": r[3],
              "chunk_index": r[4]} for r in rows]
    col.upsert(ids=ids, embeddings=vecs, metadatas=metas)


def search(query_embedding: bytes, top_k: int = 30) -> list[dict]:
    """按 query 向量检索，返回 [{id, content, file_id, score, ...}]"""
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

    from src.storage.db import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, file_id, chunk_index, content, embedding FROM chunks "
        "WHERE embedding IS NOT NULL"
    ).fetchall()

    to_add = [r for r in rows if r['id'] not in existing]
    if to_add:
        add_batch([(r['id'], r['embedding'], r['content'], r['file_id'], r['chunk_index'])
                   for r in to_add])
        logger.info(f"Chroma 补齐 {len(to_add)} 条（总数 {count()}）")
    return len(to_add)


def count() -> int:
    """返回已存向量数"""
    if not _use_chroma():
        return 0
    col = _get_collection()
    return col.count()


def _unpack(data: bytes) -> np.ndarray:
    import struct
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)
