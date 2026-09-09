"""
MCP 市场本地缓存层
=================

国内访问 smithery.ai 慢（每次翻页远程拉取 2-5s），落地「后台全量预取 + 本地查询」：

1. 后台线程一次性拉全量（约 500 条，pageSize 上限 100，分 5 页）写入 SQLite
2. 市场列表/搜索全部走本地 SQLite 查询，秒回，不再每次远程
3. 缓存过期（默认 24h）后后台刷新；刷新失败保留旧数据

缓存表：mcp_servers_cache（data/rag.db）
"""
import logging
import threading
import sqlite3
import time
import httpx
from config import DB_PATH as _DB_PATH, SQLITE_BUSY_TIMEOUT

logger = logging.getLogger("rag.mcp.local_cache")

SMITHERY_REGISTRY = "https://registry.smithery.ai/servers"
CACHE_TTL_SECONDS = 24 * 3600  # 24 小时过期

_local = threading.local()
_refresh_lock = threading.Lock()
_refreshing = False


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_servers_cache ("
            "  qualifiedName TEXT PRIMARY KEY,"
            "  displayName TEXT NOT NULL DEFAULT '',"
            "  description TEXT NOT NULL DEFAULT '',"
            "  iconUrl TEXT NOT NULL DEFAULT '',"
            "  verified INTEGER NOT NULL DEFAULT 0,"
            "  useCount INTEGER NOT NULL DEFAULT 0,"
            "  homepage TEXT NOT NULL DEFAULT '',"
            "  updated_at REAL NOT NULL"
            ")"
        )
        _local.conn = conn
    return conn


def is_cache_ready() -> bool:
    """缓存是否可用（有数据且未过期）。"""
    try:
        row = _get_conn().execute("SELECT COUNT(*), MAX(updated_at) FROM mcp_servers_cache").fetchone()
        count, last = row
        if count == 0:
            return False
        if last is None:
            return False
        return (time.time() - last) < CACHE_TTL_SECONDS
    except Exception as e:
        logger.warning(f"检查缓存状态失败: {e}")
        return False


def _fetch_all(progress_cb=None) -> list[dict]:
    """全量拉取（分页），返回规整后的列表。失败返回 []。"""
    result = []
    try:
        with httpx.Client(timeout=30) as client:
            page = 1
            while True:
                resp = client.get(SMITHERY_REGISTRY, params={"page": page, "pageSize": 100})
                resp.raise_for_status()
                data = resp.json()
                servers = data.get("servers", [])
                if not servers:
                    break
                for s in servers:
                    result.append({
                        "qualifiedName": s.get("qualifiedName") or s.get("namespace") or "",
                        "displayName": s.get("displayName") or s.get("qualifiedName") or "",
                        "description": s.get("description") or "",
                        "iconUrl": s.get("iconUrl", ""),
                        "verified": bool(s.get("verified", False)),
                        "useCount": s.get("useCount", 0),
                        "homepage": s.get("homepage", ""),
                    })
                tp = data.get("pagination", {}).get("totalPages", 0)
                if progress_cb:
                    progress_cb(page, tp)
                if tp and page >= tp:
                    break
                page += 1
                if page > 50:  # 安全上限
                    break
    except Exception as e:
        logger.warning(f"MCP 全量拉取失败: {e}")
        return []
    return result


def _write_cache(servers: list[dict]):
    conn = _get_conn()
    now = time.time()
    with conn:
        for s in servers:
            if not s.get("qualifiedName"):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO mcp_servers_cache "
                "(qualifiedName, displayName, description, iconUrl, verified, useCount, homepage, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (s["qualifiedName"], s["displayName"], s["description"], s["iconUrl"],
                 int(s["verified"]), s["useCount"], s["homepage"], now),
            )


def refresh_cache(force: bool = False):
    """后台刷新缓存的入口（非阻塞）。已在刷新中则跳过。"""
    global _refreshing
    if not force and is_cache_ready():
        return
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def _worker():
        global _refreshing
        try:
            servers = _fetch_all()
            if servers:
                _write_cache(servers)
                logger.info(f"MCP 缓存刷新完成：{len(servers)} 条")
            else:
                logger.warning("MCP 缓存刷新失败（空结果），保留旧数据")
        finally:
            _refreshing = False

    threading.Thread(target=_worker, daemon=True).start()


def query_local(q: str = "", page: int = 1, page_size: int = 20, sort_by_relevance: bool = True) -> dict:
    """本地分页 + 关键词搜索。返回 {servers, total, total_pages, page, page_size}。

    sort_by_relevance=True 时按「对 RAG 知识库的提升度」降序排序（同分按 useCount 兑底），
    否则退回按 useCount 降序。
    """
    conn = _get_conn()
    page = max(1, page)
    page_size = min(max(1, page_size), 50)

    where = ""
    params: list = []
    if q and q.strip():
        kw = f"%{q.strip()}%"
        where = "WHERE qualifiedName LIKE ? OR displayName LIKE ? OR description LIKE ?"
        params = [kw, kw, kw]

    total = conn.execute(f"SELECT COUNT(*) FROM mcp_servers_cache {where}", params).fetchone()[0]

    if sort_by_relevance:
        # 相关性排序需运行时打分，不能纯 SQL：拉全量（命中搜索的）打分后分页
        all_rows = conn.execute(
            f"SELECT qualifiedName, displayName, description, iconUrl, verified, useCount, homepage "
            f"FROM mcp_servers_cache {where}",
            params,
        ).fetchall()
        from src.mcp.relevance import score_server
        scored = []
        for r in all_rows:
            s, _ = score_server(r[0], r[1], r[2])
            scored.append((s, r[5], r))  # (score, useCount, row)
        scored.sort(key=lambda x: (-x[0], -x[1], x[2][0]))
        offset = (page - 1) * page_size
        page_rows = scored[offset:offset + page_size]
        rows = [x[2] for x in page_rows]
        # 附带相关性分给前端展示
        scores_by_qn = {x[2][0]: x[0] for x in scored}
    else:
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT qualifiedName, displayName, description, iconUrl, verified, useCount, homepage "
            f"FROM mcp_servers_cache {where} "
            f"ORDER BY useCount DESC, qualifiedName ASC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        scores_by_qn = {}

    servers = [{
        "qualifiedName": r[0],
        "displayName": r[1],
        "description": r[2],
        "iconUrl": r[3],
        "verified": bool(r[4]),
        "useCount": r[5],
        "homepage": r[6],
        "relevance": scores_by_qn.get(r[0], 0.0),
    } for r in rows]

    return {
        "servers": servers,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "page": page,
        "page_size": page_size,
    }


def cache_count() -> int:
    try:
        return _get_conn().execute("SELECT COUNT(*) FROM mcp_servers_cache").fetchone()[0]
    except Exception:
        return 0
