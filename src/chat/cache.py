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

# 语义缓存版本号：检索/生成逻辑发生实质变更时手动 bump。
# 版本不同则旧缓存整体失效，避免「检索修复后命中旧错误答案」污染对话结果。
# 变更时机示例：检索架构重构、rerank 策略改动、分类词典大改、生成 prompt 调整。
SEMANTIC_CACHE_VERSION = 2

_cache_lock = threading.Lock()
_entries: list[dict] = []  # [{query, embedding: np.ndarray, answer, sources, ts, version}]

# S13: _ensure_table 只需执行一次，后续调用直接跳过
_table_ensured = False


def _ensure_table():
    """确保 SQLite 表存在（含 version 列，兼容旧表）"""
    global _table_ensured
    if _table_ensured:
        return
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
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # S14: 为 query 列创建索引，加速 hit_count UPDATE 语句
        conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_cache_query ON semantic_cache(query)")
        # 旧表无 version 列则补上（幂等）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(semantic_cache)").fetchall()]
        if "version" not in cols:
            conn.execute("ALTER TABLE semantic_cache ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        conn.commit()
        _table_ensured = True
    except Exception as e:
        logger.warning(f"语义缓存表创建失败: {e}")


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.tolist())


def _unpack(data: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{len(data)//4}f", data), dtype=np.float32)


def _embedding_dim_of(data: bytes) -> int:
    """从 embedding bytes 反推维度（len/4，float32 每维占 4 字节）。

    缓存一致性契约：命中/存储时校验「条目向量维度 == 查询向量维度」，不一致则
    跳过该条目（视为旧 embeddong 模型产生的无效缓存），从根上避免
    「embedding 模型升级（768→1024）导致 np.dot 静默错配/崩错」。
    """
    return len(data) // 4


def load_cache():
    """启动时从 SQLite 加载缓存到内存

    加载逻辑：
    1. 版本不一致的旧缓存整条跳过（失效，不加载进内存）
    2. 向量归一化后存储到内存
    3. 按 hit_count 降序排列，优先加载高命中缓存
    """
    global _entries
    if not _use_cache():
        return
    try:
        _ensure_table()
        from src.storage.db import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT query, embedding, answer, sources, hit_count, version FROM semantic_cache "
            "ORDER BY hit_count DESC, created_at DESC LIMIT ?",
            (SEMANTIC_CACHE_MAX,)
        ).fetchall()
        _entries = []
        skipped = 0
        for r in rows:
            # 版本不一致的旧缓存整条跳过（失效，不加载进内存）
            if r["version"] != SEMANTIC_CACHE_VERSION:
                skipped += 1
                continue
            emb = _unpack(r["embedding"])
            dim = len(emb)
            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = emb / norm
            _entries.append({
                "query": r["query"],
                "embedding": emb,
                "dim": dim,
                "answer": r["answer"],
                "sources": json.loads(r["sources"] or "[]"),
                "hit_count": r["hit_count"],
                "version": r["version"],
            })
        if skipped:
            logger.info(f"语义缓存加载: {len(_entries)} 条（跳过 {skipped} 条旧版本 v1 缓存）")
        else:
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
    q_dim = _embedding_dim_of(query_embedding)

    best_sim = 0.0
    best_entry = None
    with _cache_lock:
        for entry in _entries:
            # 维度不一致的旧缓存跳过（embedding 模型升级后的无效条目）
            if entry.get("dim") is not None and entry["dim"] != q_dim:
                continue
            sim = float(np.dot(q_vec, entry["embedding"]))
            if sim > best_sim:
                best_sim = sim
                best_entry = entry

    if best_sim >= SEMANTIC_CACHE_THRESHOLD and best_entry:
        logger.info(f"语义缓存命中: sim={best_sim:.3f} query='{best_entry['query'][:30]}'")
        # 来源有效性校验：缓存 sources 指向的 chunk/file 可能已被上传/删除，
        #   命中后若来源已不存在，则视为未命中（走正常检索），避免把答案连同
        #   指向已删文档/段落的引用一并返回（数据级正确性）。
        if not _sources_still_valid(best_entry.get("sources")):
            logger.info(f"语义缓存命中但来源已过期，视为未命中: query='{best_entry['query'][:30]}'")
            return None
        # 更新命中计数
        with _cache_lock:
            best_entry["hit_count"] = best_entry.get("hit_count", 0) + 1
        _update_hit_count(best_entry["query"])
        return {"answer": best_entry["answer"], "sources": best_entry["sources"]}

    return None


def _sources_still_valid(sources: list) -> bool:
    """校验缓存来源（chunk_id/file_id）是否仍存在，全部有效返回 True。

    无来源（空列表/None）视为有效（闲聊式裸答案无锚点，不因无 sources 而误判失效）。
    来源非空时，批量查询 chunks/files 表确认存在性，
    任一来源指向已删数据则返回 False（缓存整体失效，走正常检索）。
    纯读、单次批量查询、异常降级 True（宁可放过也不因 DB 异常阻断缓存命中）。
    """
    if not sources:
        return True
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        chunk_ids = [s["chunk_id"] for s in sources if s.get("chunk_id")]
        file_ids = [s["file_id"] for s in sources if s.get("file_id") and not s.get("chunk_id")]
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            rows = conn.execute(f"SELECT id FROM chunks WHERE id IN ({placeholders})", chunk_ids).fetchall()
            found = {r["id"] for r in rows}
            if not all(cid in found for cid in chunk_ids):
                return False
        if file_ids:
            placeholders = ",".join("?" * len(file_ids))
            rows = conn.execute(f"SELECT id FROM files WHERE id IN ({placeholders})", file_ids).fetchall()
            found = {r["id"] for r in rows}
            if not all(fid in found for fid in file_ids):
                return False
        return True
    except Exception as e:
        logger.debug(f"缓存来源有效性校验异常（降级放行）: {e}")
        return True


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
        "dim": _embedding_dim_of(query_embedding),
        "answer": answer,
        "sources": sources,
        "hit_count": 0,
        "version": SEMANTIC_CACHE_VERSION,
    }

    with _cache_lock:
        # LRU 淘汰（先预留空间，避免内存写入后 SQLite 失败导致不一致）
        if len(_entries) >= SEMANTIC_CACHE_MAX:
            _entries.pop(0)

    # 持久化到 SQLite（先于内存写入，保证一致性）
    try:
        _ensure_table()
        from src.storage.db import _get_conn
        conn = _get_conn()
        conn.execute(
            "INSERT INTO semantic_cache (query, embedding, answer, sources, version) VALUES (?,?,?,?,?)",
            (query, _pack(q_vec), answer, json.dumps(sources, ensure_ascii=False), SEMANTIC_CACHE_VERSION)
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"缓存持久化失败: {e}")
        return

    # SQLite 持久化成功后才写入内存
    with _cache_lock:
        _entries.append(entry)


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


def purge_semantic_cache() -> int:
    """#10（2026-09-21）：清理 semantic_cache 持久表，防无上限增长。

    策略：保留 hit_count 最高的 SEMANTIC_CACHE_MAX 条（LRU-ish，与内存层口径一致），
    其余删除。同时清理版本不一致的旧缓存（version != SEMANTIC_CACHE_VERSION）。
    返回删除行数。
    """
    try:
        _ensure_table()
        from src.storage.db import _get_conn
        conn = _get_conn()
        removed = 0
        # 1) 清旧版本缓存
        cur = conn.execute(
            "DELETE FROM semantic_cache WHERE version IS NULL OR version != ?",
            (SEMANTIC_CACHE_VERSION,),
        )
        removed += cur.rowcount or 0
        # 2) 超上限时保留 hit_count 最高的 N 条
        total = conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
        if total > SEMANTIC_CACHE_MAX:
            cur = conn.execute(
                "DELETE FROM semantic_cache WHERE id NOT IN ("
                "  SELECT id FROM semantic_cache ORDER BY hit_count DESC, created_at DESC LIMIT ?"
                ")",
                (SEMANTIC_CACHE_MAX,),
            )
            removed += cur.rowcount or 0
        conn.commit()
        return removed
    except Exception as e:
        logger.debug(f"semantic_cache 清理失败: {e}")
        return 0
