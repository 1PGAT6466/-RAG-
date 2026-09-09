"""
语义缓存 — 相似查询复用 LLM 响应，命中时跳过检索+生成

设计：
  - 嵌入缓存查询文本，用余弦相似度匹配
  - 命中时直接返回缓存的 answer + sources
  - 存储: SQLite 表 semantic_cache（query_embedding + answer + sources）
  - 内存 LRU 热缓存（FAISS 替代：用 numpy 暴力扫描，单机万级足够）
"""
import json
import logging
import struct
import time
import threading
import numpy as np

from config import SEMANTIC_CACHE, SEMANTIC_CACHE_THRESHOLD, SEMANTIC_CACHE_MAX

logger = logging.getLogger("rag.cache")

_cache_lock = threading.Lock()
_entries: list[dict] = []  # [{query, embedding: np.ndarray, answer, sources, ts}]


def _ensure_table():
    """确保 SQLite 表存在"""
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS semantic_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                embedding BLOB NOT NULL,
                answer TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                hit_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
    except Exception as e:
        logger.warning(f"语义缓存表创建失败: {e}")


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.tolist())


def _unpack(data: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)


def load_cache():
    """启动时从 SQLite 加载缓存到内存"""
    global _entries
    if not _use_cache():
        return
    try:
        _ensure_table()
        from src.storage.db import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT query, embedding, answer, sources, hit_count FROM semantic_cache "
            "ORDER BY hit_count DESC, created_at DESC LIMIT ?",
            (SEMANTIC_CACHE_MAX,)
        ).fetchall()
        _entries = []
        for r in rows:
            emb = _unpack(r["embedding"])
            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = emb / norm
            _entries.append({
                "query": r["query"],
                "embedding": emb,
                "answer": r["answer"],
                "sources": json.loads(r["sources"] or "[]"),
                "hit_count": r["hit_count"],
            })
        logger.info(f"语义缓存加载: {len(_entries)} 条")
    except Exception as e:
        logger.warning(f"语义缓存加载失败: {e}")


def _use_cache() -> bool:
    return SEMANTIC_CACHE == "1"


def lookup(query_embedding: bytes) -> dict | None:
    """查找相似缓存。返回 {answer, sources} 或 None"""
    if not _use_cache() or not _entries:
        return None
    q_vec = np.frombuffer(query_embedding, dtype=np.float32).copy()
    norm = np.linalg.norm(q_vec)
    if norm == 0:
        return None
    q_vec = q_vec / norm

    best_sim = 0.0
    best_entry = None
    with _cache_lock:
        for entry in _entries:
            sim = float(np.dot(q_vec, entry["embedding"]))
            if sim > best_sim:
                best_sim = sim
                best_entry = entry

    if best_sim >= SEMANTIC_CACHE_THRESHOLD and best_entry:
        logger.info(f"语义缓存命中: sim={best_sim:.3f} query='{best_entry['query'][:30]}'")
        # 更新命中计数
        with _cache_lock:
            best_entry["hit_count"] = best_entry.get("hit_count", 0) + 1
        _update_hit_count(best_entry["query"])
        return {"answer": best_entry["answer"], "sources": best_entry["sources"]}

    return None


def store(query: str, query_embedding: bytes, answer: str, sources: list):
    """存储新的查询-响应对到缓存"""
    if not _use_cache():
        return
    q_vec = np.frombuffer(query_embedding, dtype=np.float32).copy()
    norm = np.linalg.norm(q_vec)
    if norm == 0:
        return
    q_vec = q_vec / norm

    entry = {
        "query": query,
        "embedding": q_vec,
        "answer": answer,
        "sources": sources,
        "hit_count": 0,
    }

    with _cache_lock:
        _entries.append(entry)
        # LRU 淘汰
        if len(_entries) > SEMANTIC_CACHE_MAX:
            _entries.pop(0)

    # 持久化到 SQLite
    try:
        _ensure_table()
        from src.storage.db import _get_conn
        conn = _get_conn()
        conn.execute(
            "INSERT INTO semantic_cache (query, embedding, answer, sources) VALUES (?,?,?,?)",
            (query, _pack(q_vec), answer, json.dumps(sources, ensure_ascii=False))
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"缓存持久化失败: {e}")


def _update_hit_count(query: str):
    """更新 SQLite 中的命中计数"""
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        conn.execute(
            "UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE query = ?",
            (query,)
        )
        conn.commit()
    except Exception:
        pass
