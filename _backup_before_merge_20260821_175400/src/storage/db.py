"""
存储层 — SQLite 统一入口
======================
schemas: files, chunks, links, users
全文索引: FTS5 (jieba 分词)
"""
import sqlite3
import json
import threading
import logging
import contextvars
from pathlib import Path
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger("rag.db")
_local = threading.local()
# 协程级连接隔离：每个 async task（contextvars 上下文）独立连接，
# 避免 uvicorn 单线程 async 模式下多个协程共享同一连接导致交织读写
_conn_var: contextvars.ContextVar[sqlite3.Connection | None] = contextvars.ContextVar('_rag_conn', default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    ext TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT '未分类',
    tags TEXT NOT NULL DEFAULT '[]',
    folder TEXT NOT NULL DEFAULT '/',
    summary TEXT NOT NULL DEFAULT '',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'keyword',
    weight REAL NOT NULL DEFAULT 1.0,
    context TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_id, target_id, link_type)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Self-contained FTS5 (no content table sync; triggers won't break on schema mismatch)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    tokenize='unicode61'
);

-- 中文子串匹配索引（trigram tokenizer，CJK 逐字切分，支持 LIKE 风格子串搜索）
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_tri USING fts5(
    content,
    tokenize='trigram'
);

-- === 阶段 2：实体与图谱 ===
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'unknown',
    aliases TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(name, type)
);

CREATE TABLE IF NOT EXISTS entity_chunks (
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    mention_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (entity_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS entity_files (
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    mention_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (entity_id, file_id)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL DEFAULT 'cooccur',
    weight REAL NOT NULL DEFAULT 1.0,
    context TEXT NOT NULL DEFAULT '',
    UNIQUE(source_id, target_id, rel_type)
);

-- 标准号分类缓存（LLM 精分类结果，避免重复烧 token）
CREATE TABLE IF NOT EXISTS standard_categories (
    name TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'llm',   -- 'rule' | 'llm'
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 对话会话（ChatGPT 式历史会话）
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,             -- 'user' | 'assistant'
    content TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'knowledge',  -- knowledge | chat | web
    sources TEXT NOT NULL DEFAULT '[]',      -- JSON: [{ref, file_name, ...}]
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conv_msg_conv ON conversation_messages(conversation_id);

CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id);
CREATE INDEX IF NOT EXISTS idx_entity_chunks_chunk ON entity_chunks(chunk_id);
CREATE INDEX IF NOT EXISTS idx_entity_files_file ON entity_files(file_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entity_rel_source ON entity_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_entity_rel_target ON entity_relations(target_id);

-- 文档图片（Obsidian 式可读模式 ![[图]] 显示，查看层增强，不影响检索）
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_images_file ON images(file_id);

-- 入库任务持久化（引擎重启后可恢复状态）
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    progress_text TEXT NOT NULL DEFAULT '',
    chunks INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT '',
    file_id INTEGER,
    error TEXT,
    created_at REAL NOT NULL
);
"""


def _new_conn() -> sqlite3.Connection:
    """创建并配置一个新的 SQLite 连接"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_conn() -> sqlite3.Connection:
    # 优先用 contextvars（async 协程隔离），回退 threading.local（后台线程）
    conn = _conn_var.get()
    if conn is not None:
        return conn
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _new_conn()
    return _local.conn


def set_conn(conn: sqlite3.Connection):
    """为当前上下文设置连接（供 async 请求中间件或测试注入）"""
    _conn_var.set(conn)


def close_thread_conn():
    """关闭当前线程的 SQLite 连接（供后台线程结束时调用）"""
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def init_db():
    """初始化数据库表结构"""
    conn = _get_conn()
    conn.executescript(SCHEMA)
    # 迁移：给旧 files 表补 summary / folder 字段（CREATE TABLE IF NOT EXISTS 不会加列）
    _migrate_add_column(conn, "files", "summary", "TEXT NOT NULL DEFAULT ''")
    _migrate_add_column(conn, "files", "folder", "TEXT NOT NULL DEFAULT '/'")
    conn.commit()


def _migrate_add_column(conn, table: str, column: str, ddl: str):
    """幂等加列：若列不存在则 ALTER TABLE 添加"""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.warning(f"迁移：{table} 表新增列 {column}")


# === 任务持久化 ===

def save_task(task: dict):
    """持久化任务状态（upsert）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tasks (task_id, filename, filepath, status, stage, progress,
           progress_text, chunks, category, file_id, error, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             status=excluded.status, stage=excluded.stage, progress=excluded.progress,
             progress_text=excluded.progress_text, chunks=excluded.chunks,
             category=excluded.category, file_id=excluded.file_id, error=excluded.error
        """,
        (
            task["task_id"], task["filename"], task.get("filepath", ""),
            task.get("status", "pending"), task.get("stage", ""),
            task.get("progress", 0), task.get("progress_text", ""),
            task.get("chunks", 0), task.get("category", ""),
            task.get("file_id"), task.get("error"),
            task.get("created_at", 0),
        ),
    )
    conn.commit()


def load_tasks() -> list[dict]:
    """加载所有持久化任务（供引擎启动时恢复内存状态）"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def mark_stale_tasks_failed():
    """启动时把上次中断的 running/pending 任务标记为 failed"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status='failed', error='服务重启，任务中断', progress_text='服务重启中断'"
        " WHERE status IN ('running', 'pending')"
    )
    conn.commit()


def delete_task(task_id: str):
    """删除任务记录（内存淘汰时同步清理 DB）"""
    conn = _get_conn()
    conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
    conn.commit()


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


# === 文件操作 ===

def add_file(name: str, path: str, ext: str = "", size: int = 0, folder: str = "/") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO files (name, path, ext, size, folder) VALUES (?,?,?,?,?)",
        (name, path, ext, size, normalize_folder(folder))
    )
    conn.commit()
    return cur.lastrowid


def get_file(file_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def list_files(category: str = None, date: str = None, folder: str = None) -> list[dict]:
    conn = _get_conn()
    # 日期筛选（近 N 天），date 形如 "7d"/"30d"/"90d"
    conds = []
    args = []
    if date:
        try:
            days = int(str(date).rstrip("d"))
            conds.append("updated_at >= datetime('now','localtime', ?)")
            args.append(f"-{days} days")
        except ValueError:
            pass
    if category:
        conds.append("category = ?")
        args.append(category)
    if folder is not None:
        f = normalize_folder(folder)
        if f == "/":
            # 根目录：仅未归入任何子目录的文件
            conds.append("folder = '/'")
        else:
            # 子目录：精确匹配或在其下的子目录（用前缀匹配）
            conds.append("(folder = ? OR folder LIKE ?)")
            args.append(f)
            args.append(f + "/%")
    where = " WHERE " + " AND ".join(conds) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM files{where} ORDER BY updated_at DESC", tuple(args)
    ).fetchall()
    return [dict(r) for r in rows]


def list_files_with_entities(category: str = None, model: str = None, material: str = None, date: str = None, folder: str = None) -> list[dict]:
    """文件列表 + 每个文件关联的实体（型号/材料），支持按型号/材料/日期筛选（阶段 3 字段筛选）

    返回 list_files 的结果，每个文件额外带：
      - models: list[str]   连接器型号实体（type=connector）
      - materials: list[str] 材料实体（type=material）
    """
    conn = _get_conn()
    files = list_files(category=category, date=date, folder=folder)
    if not files:
        return []
    file_ids = [f["id"] for f in files]
    placeholders = ",".join("?" * len(file_ids))

    # 聚合每个文件的型号 + 材料实体
    rows = conn.execute(
        f"""
        SELECT ef.file_id, e.type, e.name
        FROM entity_files ef
        JOIN entities e ON e.id = ef.entity_id
        WHERE ef.file_id IN ({placeholders}) AND e.type IN ('connector','material')
        ORDER BY ef.mention_count DESC
        """,
        tuple(file_ids),
    ).fetchall()

    # 组装
    file_map = {f["id"]: f for f in files}
    for f in files:
        f["models"] = []
        f["materials"] = []
    for r in rows:
        f = file_map.get(r["file_id"])
        if not f:
            continue
        if r["type"] == "connector":
            if r["name"] not in f["models"]:
                f["models"].append(r["name"])
        elif r["type"] == "material":
            if r["name"] not in f["materials"]:
                f["materials"].append(r["name"])

    # 按型号/材料过滤
    if model:
        files = [f for f in files if model in f["models"]]
    if material:
        files = [f for f in files if material in f["materials"]]
    return files


def update_file_category(file_id: int, category: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET category=?, updated_at=datetime('now','localtime') WHERE id=?",
        (category, file_id)
    )
    conn.commit()


def update_file_tags(file_id: int, tags: list[str]):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET tags=?, updated_at=datetime('now','localtime') WHERE id=?",
        (_dumps(tags), file_id)
    )
    conn.commit()


def update_file_summary(file_id: int, summary: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET summary=?, updated_at=datetime('now','localtime') WHERE id=?",
        (summary or "", file_id)
    )
    conn.commit()


def update_file_folder(file_id: int, folder: str):
    """移动文件到指定目录（虚拟目录，Obsidian 式文件夹树）"""
    conn = _get_conn()
    folder = normalize_folder(folder)
    conn.execute(
        "UPDATE files SET folder=?, updated_at=datetime('now','localtime') WHERE id=?",
        (folder, file_id)
    )
    conn.commit()


def normalize_folder(folder: str) -> str:
    """规范化目录路径：去首尾空格，确保以 / 开头、无末尾斜杠"""
    folder = (folder or "").strip().replace("\\", "/")
    if not folder:
        return "/"
    if not folder.startswith("/"):
        folder = "/" + folder
    folder = folder.rstrip("/")
    if not folder:
        return "/"
    return folder


def list_folders() -> list[dict]:
    """构建目录树：返回 [{ name, path, count, children: [...] }]，含文件计数"""
    conn = _get_conn()
    rows = conn.execute("SELECT folder FROM files").fetchall()
    tree = {"children": {}, "count": 0}
    for (folder,) in rows:
        folder = normalize_folder(folder)
        parts = [p for p in folder.split("/") if p]
        node = tree
        for i, p in enumerate(parts):
            full = "/" + "/".join(parts[: i + 1])
            if p not in node["children"]:
                node["children"][p] = {"children": {}, "count": 0, "path": full}
            node = node["children"][p]
        node["count"] += 1

    def _build(node):
        return [
            {
                "name": name,
                "path": child["path"],
                "count": child["count"],
                "children": _build(child),
            }
            for name, child in sorted(node["children"].items())
        ]

    return _build(tree)


def delete_file(file_id: int):
    conn = _get_conn()
    # 先收集需要清理的数据（在删除之前）
    chunk_ids = conn.execute("SELECT id FROM chunks WHERE file_id=?", (file_id,)).fetchall()
    img_paths = conn.execute("SELECT path FROM images WHERE file_id=?", (file_id,)).fetchall()
    # 清理 FTS（两个索引）
    for (cid,) in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM chunks_fts_tri WHERE rowid=?", (cid,))
    # 删除文件（CASCADE 自动删除 chunks / images / entity_chunks / entity_files）
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()

    # 删磁盘图片目录
    try:
        import shutil
        from config import IMAGES_DIR
        img_dir = IMAGES_DIR / str(file_id)
        if img_dir.exists():
            shutil.rmtree(str(img_dir), ignore_errors=True)
    except Exception as e:
        logger.warning(f"清理图片目录失败（已忽略）: {e}")

    # ChromaDB 向量清理
    try:
        from src.storage.chroma_store import delete, _use_chroma
        if _use_chroma():
            for (cid,) in chunk_ids:
                delete(cid)
    except Exception as e:
        logger.warning(f"ChromaDB 删除失败（已忽略）: {e}")


def sync_chunk_count(file_id: int):
    """同步文件的 chunk_count"""
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE file_id = ?) WHERE id = ?",
        (file_id, file_id)
    )
    conn.commit()


# === 图片操作 ===

def add_images(file_id: int, images: list[dict]) -> int:
    """批量插入图片记录，返回插入数量"""
    conn = _get_conn()
    n = 0
    for img in images:
        conn.execute(
            "INSERT INTO images (file_id, page, path, filename, width, height) VALUES (?,?,?,?,?,?)",
            (file_id, img.get("page", 0), img.get("path", ""),
             img.get("filename", ""), img.get("width", 0), img.get("height", 0))
        )
        n += 1
    conn.commit()
    return n


def list_images(file_id: int) -> list[dict]:
    """返回文件的图片列表（按 page 排序）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM images WHERE file_id=? ORDER BY page, id", (file_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def count_images(file_id: int) -> int:
    conn = _get_conn()
    return conn.execute("SELECT COUNT(*) FROM images WHERE file_id=?", (file_id,)).fetchone()[0]


# === 对话会话操作 ===

def create_conversation(user_id: int, title: str = "新对话") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (user_id, title) VALUES (?,?)", (user_id, title)
    )
    conn.commit()
    return cur.lastrowid


def list_conversations(user_id: int) -> list[dict]:
    """返回用户的会话列表（按更新时间倒序），带消息数"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.id, c.title, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS msg_count
           FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: int, user_id: int = None) -> dict | None:
    conn = _get_conn()
    if user_id:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return dict(row) if row else None


def get_conversation_messages(conversation_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # sources 反序列化
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        result.append(d)
    return result


def add_conversation_message(conversation_id: int, role: str, content: str,
                             mode: str = "knowledge", sources: list = None) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, role, content, mode, sources) VALUES (?,?,?,?,?)",
        (conversation_id, role, content, mode, _dumps(sources or [])),
    )
    conn.execute(
        "UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?",
        (conversation_id,),
    )
    conn.commit()
    return cur.lastrowid


def update_conversation_title(conversation_id: int, title: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE conversations SET title=?, updated_at=datetime('now','localtime') WHERE id=?",
        (title, conversation_id),
    )
    conn.commit()


def delete_conversation(conversation_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
    conn.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conversation_id,))
    conn.commit()


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
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (rowid, segment_for_fts(row[2]))
            )
            # trigram 索引：中文子串匹配
            conn.execute(
                "INSERT INTO chunks_fts_tri(rowid, content) VALUES (?, ?)",
                (rowid, row[2])
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


# === FTS5 全文搜索 ===

def _to_fts_query(text: str) -> str:
    """中文查询 → FTS5 兼容查询（jieba 分词，回退 bigram）"""
    from src.storage.tokenizer import to_fts_query
    return to_fts_query(text)


def fts_search(query: str, limit: int = 20) -> list[dict]:
    """FTS5 BM25 全文搜索（双索引：jieba 分词 + trigram 子串），返回 chunk + 文件名"""
    conn = _get_conn()
    fts_query = _to_fts_query(query)

    # 主索引：jieba 分词 + unicode61（词级匹配）
    rows = conn.execute(
        "SELECT c.id, c.content, c.file_id, c.chunk_index, f.name as file_name, "
        "fts.rank as score FROM chunks_fts fts "
        "JOIN chunks c ON c.id = fts.rowid "
        "JOIN files f ON f.id = c.file_id "
        f"WHERE chunks_fts MATCH '{fts_query}' ORDER BY rank LIMIT ?",
        (limit,)
    ).fetchall()
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
                f"WHERE chunks_fts_tri MATCH '\"{safe_query}\"' ORDER BY rank LIMIT ?",
                (limit,)
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
    """返回知识图谱数据：节点 + 边"""
    conn = _get_conn()
    nodes = conn.execute(
        "SELECT id, name, category, chunk_count, "
        "CAST(id AS TEXT) as group_id FROM files"
    ).fetchall()
    edges = conn.execute(
        "SELECT source_id, target_id, link_type, weight, context FROM links"
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


# === 阶段 2：实体与图谱操作 ===

def upsert_entity(name: str, etype: str = "unknown", aliases: list = None,
                  description: str = "", attributes: dict = None) -> int:
    """插入或更新实体，返回实体 id"""
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id FROM entities WHERE name=? AND type=?", (name, etype)
    ).fetchone()
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
    if existing:
        conn.execute(
            "UPDATE entities SET aliases=?, description=?, attributes=? WHERE id=?",
            (aliases_json, description, attrs_json, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO entities (name, type, aliases, description, attributes) "
        "VALUES (?,?,?,?,?)",
        (name, etype, aliases_json, description, attrs_json),
    )
    conn.commit()
    return cur.lastrowid


def add_entity_chunk(entity_id: int, chunk_id: int, mention_count: int = 1) -> None:
    """记录实体-分块关联（累加 mention_count）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_chunks (entity_id, chunk_id, mention_count)
        VALUES (?,?,?)
        ON CONFLICT(entity_id, chunk_id) DO UPDATE SET
            mention_count = entity_chunks.mention_count + excluded.mention_count
        """,
        (entity_id, chunk_id, mention_count),
    )
    conn.commit()


def add_entity_file(entity_id: int, file_id: int, mention_count: int = 1) -> None:
    """记录实体-文件关联（累加）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_files (entity_id, file_id, mention_count)
        VALUES (?,?,?)
        ON CONFLICT(entity_id, file_id) DO UPDATE SET
            mention_count = entity_files.mention_count + excluded.mention_count
        """,
        (entity_id, file_id, mention_count),
    )
    conn.commit()


def add_entity_relation(source_id: int, target_id: int, rel_type: str = "cooccur",
                        weight: float = 1.0, context: str = "") -> None:
    """实体间关系（重复则累加权重）"""
    if source_id == target_id:
        return
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_relations (source_id, target_id, rel_type, weight, context)
        VALUES (?,?,?,?,?)
        ON CONFLICT(source_id, target_id, rel_type) DO UPDATE SET
            weight = entity_relations.weight + excluded.weight
        """,
        (source_id, target_id, rel_type, weight, context),
    )
    conn.commit()


def list_entities(etype: str = None, limit: int = 500) -> list[dict]:
    """实体列表，可按 type 过滤"""
    conn = _get_conn()
    if etype:
        rows = conn.execute(
            "SELECT * FROM entities WHERE type=? ORDER BY id", (etype,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entities ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_entity(entity_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def get_entity_by_name(name: str, etype: str = None) -> dict | None:
    conn = _get_conn()
    if etype:
        row = conn.execute("SELECT * FROM entities WHERE name=? AND type=?", (name, etype)).fetchone()
    else:
        row = conn.execute("SELECT * FROM entities WHERE name=? LIMIT 1", (name,)).fetchone()
    return dict(row) if row else None


def get_entity_chunks(entity_id: int) -> list[dict]:
    """反链：实体被哪些 chunk 提到"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT ec.chunk_id, ec.mention_count, c.content, c.file_id, c.chunk_index, f.name as file_name
        FROM entity_chunks ec
        JOIN chunks c ON c.id = ec.chunk_id
        JOIN files f ON f.id = c.file_id
        WHERE ec.entity_id=? ORDER BY ec.mention_count DESC
        """,
        (entity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_files(entity_id: int) -> list[dict]:
    """反链：实体出现在哪些文件"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT ef.file_id, ef.mention_count, f.name as file_name, f.category
        FROM entity_files ef
        JOIN files f ON f.id = ef.file_id
        WHERE ef.entity_id=? ORDER BY ef.mention_count DESC
        """,
        (entity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_standard_category(name: str) -> str | None:
    """查询标准号分类缓存"""
    conn = _get_conn()
    row = conn.execute("SELECT category FROM standard_categories WHERE name=?", (name,)).fetchone()
    return row["category"] if row else None


def set_standard_category(name: str, category: str, source: str = "llm") -> None:
    """写入标准号分类缓存（upsert）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO standard_categories (name, category, source, updated_at)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(name) DO UPDATE SET category=excluded.category, source=excluded.source, updated_at=datetime('now')""",
        (name, category, source),
    )
    conn.commit()


def get_entity_spec_params(entity_id: int) -> list[dict]:
    """实体的规格参数（spec 边关联的 param 实体），用于连接器规格卡"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT e.id, e.name, e.attributes, er.weight
        FROM entity_relations er
        JOIN entities e ON e.id = CASE
            WHEN er.source_id=? THEN er.target_id
            ELSE er.source_id
        END
        WHERE er.rel_type='spec' AND (er.source_id=? OR er.target_id=?)
          AND e.type='param'
        ORDER BY er.weight DESC
        """,
        (entity_id, entity_id, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_relations(entity_id: int, rel_types: list[str] | None = None) -> list[dict]:
    """实体的语义关系（排除 cooccur），用于实体详情面板展示

    返回该实体作为 source 或 target 的所有非 cooccur 关系，带方向标注。
    rel_types: 可选，只返回指定关系类型。
    """
    conn = _get_conn()
    if rel_types:
        placeholders = ",".join("?" * len(rel_types))
        rows = conn.execute(
            f"""
            SELECT er.source_id, er.target_id, er.rel_type, er.weight, er.context,
                   s.name AS source_name, s.type AS source_type,
                   t.name AS target_name, t.type AS target_type
            FROM entity_relations er
            JOIN entities s ON s.id = er.source_id
            JOIN entities t ON t.id = er.target_id
            WHERE er.rel_type IN ({placeholders})
              AND (er.source_id=? OR er.target_id=?)
            ORDER BY er.weight DESC
            """,
            (*rel_types, entity_id, entity_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT er.source_id, er.target_id, er.rel_type, er.weight, er.context,
                   s.name AS source_name, s.type AS source_type,
                   t.name AS target_name, t.type AS target_type
            FROM entity_relations er
            JOIN entities s ON s.id = er.source_id
            JOIN entities t ON t.id = er.target_id
            WHERE er.rel_type != 'cooccur'
              AND (er.source_id=? OR er.target_id=?)
            ORDER BY er.weight DESC
            """,
            (entity_id, entity_id),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 标注方向：当前实体是 source(target→source) 还是 target(source→target)
        if r["source_id"] == entity_id:
            d["direction"] = "out"
            d["other_name"] = r["target_name"]
            d["other_type"] = r["target_type"]
        else:
            d["direction"] = "in"
            d["other_name"] = r["source_name"]
            d["other_type"] = r["source_type"]
        result.append(d)
    return result


def get_entity_graph(types: list[str] | None = None) -> dict:
    """返回实体关系图：实体节点 + 关系边。节点带 degree（度，用于前端渲染大小）

    types: 可选，按实体类型过滤（只保留含至少一个指定类型的边）
    """
    conn = _get_conn()
    if types:
        placeholders = ",".join("?" * len(types))
        nodes = conn.execute(
            f"SELECT id, name, type, attributes, CAST(id AS TEXT) as group_id FROM entities WHERE type IN ({placeholders})",
            tuple(types),
        ).fetchall()
    else:
        nodes = conn.execute(
            "SELECT id, name, type, attributes, CAST(id AS TEXT) as group_id FROM entities"
        ).fetchall()
    node_list = [dict(n) for n in nodes]
    node_ids = {n["id"] for n in node_list}
    all_edges = conn.execute(
        "SELECT source_id, target_id, rel_type, weight, context FROM entity_relations"
    ).fetchall()
    # 只保留两端节点都在过滤结果里的边
    edge_list = [
        dict(e) for e in all_edges
        if e["source_id"] in node_ids and e["target_id"] in node_ids
    ]
    # 计算节点度数（连接边数）
    degree = {}
    for e in edge_list:
        degree[e["source_id"]] = degree.get(e["source_id"], 0) + 1
        degree[e["target_id"]] = degree.get(e["target_id"], 0) + 1
    for n in node_list:
        n["degree"] = degree.get(n["id"], 0)
    return {
        "nodes": node_list,
        "edges": edge_list,
    }
