"""Chunk + FTS5 搜索 + 链接操作"""
import logging

from src.storage.connection import _get_conn, _dumps

logger = logging.getLogger('rag.db.chunks')


def _sanitize_fts_content(text: str) -> str:
    """C5: 清理 FTS5 特殊字符，防止 trigram 索引脏数据。"""
    import re
    # 移除 FTS5 控制字符，保留中文/英文/数字/标点
    return re.sub(r'[\x00-\x1f*^:"{}\[\]()]', ' ', text)


# === Chunk 操作 ===

def add_chunks_batch(rows: list[tuple]) -> list[int]:
    """批量插入 [(file_id, idx, content, token_count, embedding_bytes, metadata_dict), ...]
    返回 chunk 真实 id 列表

    用显式事务包住（with conn: 自动 BEGIN/COMMIT），一次提交减少写锁窗口；
    逐条 INSERT 并取真实 rowid（executemany 的 lastrowid 不可靠，见 MEMORY）。
    """
    conn = _get_conn()
    data = [(r[0], r[1], r[2], r[3], r[4], _dumps(r[5] or {})) for r in rows]
    from src.storage.tokenizer import segment_for_fts
    chunk_ids = []
    with conn:  # 显式事务：批量提交，缩短写锁持有时间
        for row in data:
            cur = conn.execute(
                "INSERT INTO chunks (file_id, chunk_index, content, token_count, embedding, metadata) "
                "VALUES (?,?,?,?,?,?)", row
            )
            rowid = cur.lastrowid
            # INSERT OR REPLACE：覆盖可能残留的同 rowid FTS 记录（防止脏数据导致
            # sqlite3.IntegrityError: constraint failed，见 MEMORY 关于 trigram 孤儿数据）
            conn.execute(
                "INSERT OR REPLACE INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (rowid, segment_for_fts(row[2]))
            )
            # trigram 索引：中文子串匹配（C5: 预处理特殊字符）
            conn.execute(
                "INSERT OR REPLACE INTO chunks_fts_tri(rowid, content) VALUES (?, ?)",
                (rowid, _sanitize_fts_content(row[2]))
            )
            chunk_ids.append(rowid)
    return chunk_ids


def get_chunks_by_file(file_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, chunk_index, content, token_count, metadata FROM chunks "
        "WHERE file_id=? ORDER BY chunk_index", (file_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def reconcile_fts(conn=None) -> dict:
    """#9（2026-09-21）：FTS5 与 chunks 主表对账，清理孤儿关系。

    背景：chunks_fts 是虚拟表，SCHEMA 无 trigger，同步靠代码显式调用。
    由于 FTS 需经 jieba 分词（segment_for_fts）写入，SQLite trigger 无法调 Python，
    故采用方案 B：提供对账函数，启动/定期调用，修补两条不一致：
      1. chunks_fts 有、chunks 主表无 → 孤儿全文条目 → 删 FTS
      2. chunks 主表有、FTS 无 → 漏索引 chunk → 补 FTS（重新分词）

    返回 {'orphan_fts': n, 'missing_fts': n}。
    """
    own = conn is None
    if own:
        conn = _get_conn()
    from src.storage.tokenizer import segment_for_fts
    stats = {"orphan_fts": 0, "missing_fts": 0}
    try:
        chunk_ids = {r[0] for r in conn.execute("SELECT id FROM chunks").fetchall()}
        fts_ids = {r[0] for r in conn.execute("SELECT rowid FROM chunks_fts").fetchall()}
        tri_ids = {r[0] for r in conn.execute("SELECT rowid FROM chunks_fts_tri").fetchall()}
        # 1) 孤儿 FTS（两索引都要清）
        orphans = (fts_ids | tri_ids) - chunk_ids
        for oid in orphans:
            conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (oid,))
            conn.execute("DELETE FROM chunks_fts_tri WHERE rowid=?", (oid,))
        stats["orphan_fts"] = len(orphans)
        # 2) 漏索引 chunk
        missing = chunk_ids - fts_ids
        for mid in missing:
            row = conn.execute("SELECT content FROM chunks WHERE id=?", (mid,)).fetchone()
            if not row:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (mid, segment_for_fts(row[0])),
            )
            conn.execute(
                "INSERT OR REPLACE INTO chunks_fts_tri(rowid, content) VALUES (?, ?)",
                (mid, _sanitize_fts_content(row[0])),
            )
        stats["missing_fts"] = len(missing)
        conn.commit()
        if orphans or missing:
            logger.warning(f"FTS 对账：清理孤儿 {len(orphans)} 条，补建 {len(missing)} 条")
    except Exception as e:
        logger.error(f"FTS 对账失败: {e}")
    return stats


# === FTS5 全文搜索 ===

def _to_fts_query(text: str) -> str:
    """中文查询 → FTS5 兼容查询（jieba 分词，回退 bigram）"""
    from src.storage.tokenizer import to_fts_query
    return to_fts_query(text)


def _sanitize_fts_term(text: str) -> str:
    """转义 FTS5 短语内的特殊字符，委托 tokenizer。"""
    from src.storage.tokenizer import _sanitize_fts_term as _sanitize
    return _sanitize(text)


def fts_search(query: str, limit: int = 20) -> list[dict]:
    """FTS5 BM25 全文搜索（双索引：jieba 分词 + trigram 子串），返回 chunk + 文件名"""
    conn = _get_conn()
    fts_query = _to_fts_query(query)
    # 缺陷B（2026-09-21）：FTS 调试日志降为 debug，避免每次检索打 WARNING（含 query 原文）
    logger.debug(f"[FTS_DEBUG] query={query!r} fts_query={fts_query!r} conn={id(conn)}")

    # 主索引：jieba 分词 + unicode61（词级匹配）
    # MATCH 用 ? 参数绑定（FTS5 官方推荐），根除 f-string 拼接的注入/语法错误面。
    try:
        rows = conn.execute(
            "SELECT c.id, c.content, c.file_id, c.chunk_index, f.name as file_name, "
            "fts.rank as score FROM chunks_fts fts "
            "JOIN chunks c ON c.id = fts.rowid "
            "JOIN files f ON f.id = c.file_id "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, limit)
        ).fetchall()
        logger.debug(f"[FTS_DEBUG] main index rows={len(rows)} for query={query!r}")
    except Exception as e:
        logger.error(f"[FTS_DEBUG] main index error: {e}")
        rows = []
    results = {r["id"]: dict(r) for r in rows}

    # 补充索引：trigram（CJK 子串匹配，捕获分词遗漏的短语）
    safe_query = _sanitize_fts_term(query)
    if safe_query and len(safe_query) >= 2:
        try:
            tri_rows = conn.execute(
                "SELECT c.id, c.content, c.file_id, c.chunk_index, f.name as file_name, "
                "fts.rank as score FROM chunks_fts_tri fts "
                "JOIN chunks c ON c.id = fts.rowid "
                "JOIN files f ON f.id = c.file_id "
                "WHERE chunks_fts_tri MATCH ? ORDER BY rank LIMIT ?",
                (f"\"{safe_query}\"", limit)
            ).fetchall()
            for r in tri_rows:
                rid = r["id"]
                if rid not in results:
                    results[rid] = dict(r)
                else:
                    # 两个索引都命中：取更优分数
                    if r["score"] < results[rid]["score"]:
                        results[rid]["score"] = r["score"]
        except Exception as e:
            logger.debug(f"trigram FTS 查询失败（已忽略）: {e}")

    # 按 score 排序（FTS5 rank 越小越好）
    merged = sorted(results.values(), key=lambda x: x.get("score", 0))
    return merged[:limit]


# === 链接操作 ===

def add_link(source_id: int, target_id: int, link_type: str = "keyword",
             weight: float = 1.0, context: str = "") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT OR REPLACE INTO links (source_id, target_id, link_type, weight, context) "
        "VALUES (?,?,?,?,?)",
        (source_id, target_id, link_type, weight, context)
    )
    conn.commit()
    return cur.lastrowid


def get_graph_data() -> dict:
    """返回知识图谱数据：节点 + 边。

    只返回两端都对应现存文件的边（JOIN files 过滤孤儿边）。
    links 表历史上曾混入 chunk_id 级链接、且文件删除时无外键级联清理，
    残留的孤儿边会让前端 d3 forceLink 抛 `node not found` 导致整图空白。
    """
    conn = _get_conn()
    nodes = conn.execute(
        "SELECT id, name, category, chunk_count, "
        "CAST(id AS TEXT) as group_id FROM files WHERE deleted_at IS NULL"
    ).fetchall()
    edges = conn.execute(
        "SELECT l.source_id, l.target_id, l.link_type, l.weight, l.context "
        "FROM links l "
        "JOIN files fs ON fs.id = l.source_id AND fs.deleted_at IS NULL "
        "JOIN files ft ON ft.id = l.target_id AND ft.deleted_at IS NULL"
    ).fetchall()
    return {
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges]
    }


def get_file_backlinks(file_id: int) -> dict:
    """文件反向链接：哪些文件与当前文件关联（Obsidian 式 backlinks）。
    返回 outgoing（当前文件指向的）与 incoming（指向当前文件的）两个方向关联。"""
    conn = _get_conn()
    # 指向当前文件的（别人引用我）
    incoming = conn.execute(
        "SELECT l.source_id, l.link_type, l.weight, l.context, "
        "f.name AS source_name, f.category AS source_category "
        "FROM links l JOIN files f ON f.id = l.source_id "
        "WHERE l.target_id = ? ORDER BY l.weight DESC",
        (file_id,)
    ).fetchall()
    # 当前文件指向的（我引用别人）
    outgoing = conn.execute(
        "SELECT l.target_id, l.link_type, l.weight, l.context, "
        "f.name AS target_name, f.category AS target_category "
        "FROM links l JOIN files f ON f.id = l.target_id "
        "WHERE l.source_id = ? ORDER BY l.weight DESC",
        (file_id,)
    ).fetchall()
    return {
        "incoming": [dict(r) for r in incoming],
        "outgoing": [dict(r) for r in outgoing],
    }
