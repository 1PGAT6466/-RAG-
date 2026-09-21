"""
MCP 市场元数据中文化翻译层
==========================

对 Smithery registry 拉回的英文 displayName / description 做中文化。

设计目标（本轮重写）：**绝不让用户翻页时干等 LLM 翻译**。

分两条路径：
1. 同步快路径（translate_servers）：只做「词典命中 + SQLite 缓存命中」，
   立即返回（<1ms），未命中的英文原文原样返回。
2. 后台异步补译（schedule_translate）：未命中的英文描述交给后台线程做 LLM
   批量翻译，翻译完写入 SQLite 缓存。前端拿到「部分英文」后渲染，几秒后
   重新拉取当前页即可命中缓存、自动变成中文。

缓存用 SQLite（data/rag.db 的 mcp_translations 表）持久化，服务重启不丢，
翻译一次永久生效。
"""
import logging
import threading
import sqlite3
import os

logger = logging.getLogger("rag.mcp.translate")

# ---- 内置词典：热门 MCP server 名称 -> 中文名 ----
NAME_DICT = {
    "brave search": "Brave 搜索",
    "gmail": "Gmail 邮件",
    "github": "GitHub",
    "google maps": "Google 地图",
    "google drive": "Google 云盘",
    "google calendar": "Google 日历",
    "postgres": "PostgreSQL 数据库",
    "mysql": "MySQL 数据库",
    "sqlite": "SQLite 数据库",
    "filesystem": "文件系统",
    "memory": "记忆存储",
    "fetch": "网页抓取",
    "puppeteer": "浏览器自动化",
    "playwright": "浏览器自动化",
    "slack": "Slack 协作",
    "notion": "Notion 笔记",
    "airtable": "Airtable 表格",
    "discord": "Discord 社区",
    "serper": "Serper 搜索",
    "tavily": "Tavily 搜索",
    "weather": "天气查询",
    "sequential thinking": "逐步思考",
    "sequential-thinking": "逐步思考",
    "time": "时间工具",
    "everything": "文件检索",
    "github actions": "GitHub Actions",
    "subwayinfo nyc": "纽约地铁信息",
    "onesignal": "OneSignal 推送",
    "agent news": "智能体新闻",
}

# ---- SQLite 持久化缓存 ----
from config import DB_PATH as _DB_PATH, SQLITE_BUSY_TIMEOUT

_init_lock = threading.Lock()
_conn_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """按线程取连接（后台线程与请求线程隔离连接，避免跨线程用同一连接）。"""
    conn = getattr(_conn_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_translations ("
            "  src TEXT PRIMARY KEY,"
            "  dst TEXT NOT NULL,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        _conn_local.conn = conn
    return conn


def _cache_get(src: str) -> str | None:
    try:
        row = _get_conn().execute("SELECT dst FROM mcp_translations WHERE src=?", (src,)).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.warning(f"翻译缓存读取失败: {e}")
        return None


def _cache_set(src: str, dst: str):
    try:
        conn = _get_conn()
        with conn:
            conn.execute("INSERT OR REPLACE INTO mcp_translations (src, dst) VALUES (?,?)", (src, dst))
    except Exception as e:
        logger.warning(f"翻译缓存写入失败: {e}")


# 内存快速缓存（避免每条都打 SQLite）
_mem: dict[str, str] = {}
_mem_giveup: set[str] = set()
_mem_lock = threading.Lock()


def _lookup_name(display: str, qualified: str) -> str | None:
    """词典命中返回中文名，否则 None。"""
    key = (display or qualified or "").strip().lower()
    for k, v in NAME_DICT.items():
        if k == key or k in key or key in k:
            return v
    return None


def _is_zh(s: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)


def _resolve(src: str) -> str:
    """返回 src 的中文（命中内存→SQLite→None）。"""
    if _is_zh(src):
        return src
    with _mem_lock:
        if src in _mem:
            return _mem[src]
        if src in _mem_giveup:
            return src  # 已确认翻译失败，直接原文（视为"已处理"，不影响 pending 判定）
    cached = _cache_get(src)
    if cached:
        with _mem_lock:
            _mem[src] = cached
        return cached
    return None


# ==================== 同步快路径 ====================

def translate_servers(servers: list[dict]) -> tuple[list[dict], bool]:
    """
    同步翻译（快路径）：只做词典 + 缓存命中，立即返回。
    返回 (翻译后的 servers, 是否还有未翻译项)。
    """
    pending_desc: list[str] = []
    new_servers = []
    has_pending = False

    for s in servers:
        ns = dict(s)
        display = s.get("displayName") or ""
        qualified = s.get("qualifiedName") or ""

        cn_name = _lookup_name(display, qualified)
        if cn_name:
            ns["displayName"] = cn_name
        elif display and not _is_zh(display):
            # 名称也可尝试缓存（历史可能翻译过名称）
            cn = _resolve(display)
            if cn and cn != display:
                ns["displayName"] = cn

        desc = s.get("description") or ""
        if desc and desc.strip():
            cn = _resolve(desc.strip())
            if cn and cn != desc.strip():
                ns["description"] = cn
            elif not _is_zh(desc.strip()):
                # 未命中，保持英文原文，标记 pending
                has_pending = True
                pending_desc.append(desc.strip())

        new_servers.append(ns)

    if pending_desc:
        schedule_translate(pending_desc)

    return new_servers, has_pending


# ==================== 后台异步补译 ====================

_translate_queue: list[str] = []
_queue_lock = threading.Lock()
_worker_started = False


def schedule_translate(texts: list[str]):
    """把未翻译文本加入后台翻译队列，触发后台线程（幂等，不阻塞）。"""
    global _worker_started
    uniq = [t for t in dict.fromkeys(texts) if t and t.strip() and not _is_zh(t)]
    if not uniq:
        return
    # 过滤已缓存的
    todo = []
    for t in uniq:
        with _mem_lock:
            if t in _mem or t in _mem_giveup:
                continue
        if _cache_get(t):
            continue
        todo.append(t)
    if not todo:
        return

    with _queue_lock:
        _translate_queue.extend(todo)
        if _worker_started:
            return
        _worker_started = True

    t = threading.Thread(target=_translate_worker, daemon=True)
    t.start()


def _translate_worker():
    """后台翻译线程：持续消费队列，每次批量 LLM 翻译一批，写入缓存。"""
    import time
    while True:
        with _queue_lock:
            if not _translate_queue:
                _worker_started = False
                return
            # 取出当前积累的一批（去重）
            batch = list(dict.fromkeys(_translate_queue))
            _translate_queue.clear()

        try:
            translated = _llm_translate_batch(batch)
        except Exception as e:
            logger.warning(f"后台翻译批次失败: {e}")
            translated = {}

        for src in batch:
            dst = translated.get(src)
            if dst and dst.strip() and dst.strip() != src:
                _cache_set(src, dst.strip())
                with _mem_lock:
                    _mem[src] = dst.strip()
            else:
                with _mem_lock:
                    _mem_giveup.add(src)
        # 队列空了稍等，避免空转
        time.sleep(0.2)

    # S23: 关闭 thread-local 的 SQLite 连接，避免泄漏
    conn = getattr(_conn_local, "conn", None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
        _conn_local.conn = None


def _llm_translate_batch(texts: list[str]) -> dict[str, str]:
    """用 DeepSeek flash 批量翻译，返回 {原文: 译文}，失败返回空 dict。"""
    from src.llm_client import call_llm_sync

    uniq = list(dict.fromkeys(t for t in texts if t and t.strip() and not _is_zh(t)))
    if not uniq:
        return {}

    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(uniq))
    system = (
        "你是技术翻译助手。把下面每行 [编号] 开头的一段英文（MCP 工具/server 的名称或简介）"
        "翻译成简洁的中文。严格按 [编号] 中文译文 的格式逐行输出，编号必须与输入一一对应，"
        "不要输出任何解释或额外内容。若某行已是中文/无法翻译，原样返回该行。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": numbered},
    ]
    content = call_llm_sync(messages, max_tokens=4096, prefer_mimo=False)
    if not content:
        return {}

    result: dict[str, str] = {}
    import re
    for line in content.splitlines():
        m = re.match(r"\s*\[(\d+)\]\s*(.+)", line)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(uniq):
                result[uniq[idx]] = m.group(2).strip()
    return result
