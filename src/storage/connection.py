"""
SQLite 连接管理（公共底层模块）
==============================
职责：_new_conn / _get_conn / set_conn / reset_conn / close_thread_conn / init_db / SCHEMA / 迁移。
所有 storage 子模块（files/chunks/entities/conversations/tasks/feedback）单向依赖本模块。
本模块不依赖任何 src.storage 子模块，彻底消除循环依赖。
"""
import sqlite3
import json
import threading
import logging
import contextvars
from config import DB_PATH, SQLITE_BUSY_TIMEOUT

logger = logging.getLogger("rag.db")
_local = threading.local()
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'keyword',
    weight REAL NOT NULL DEFAULT 1.0,
    context TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id, link_type)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_tri USING fts5(content, tokenize='trigram');
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'unknown',
    aliases TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
CREATE TABLE IF NOT EXISTS standard_categories (
    name TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'llm',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'knowledge',
    sources TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conv_msg_conv ON conversation_messages(conversation_id);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    conversation_id INTEGER,
    message_id INTEGER,
    query TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'down',
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_chunk ON feedback(kind);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file_idx ON chunks(file_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id);
CREATE INDEX IF NOT EXISTS idx_entity_chunks_chunk ON entity_chunks(chunk_id);
CREATE INDEX IF NOT EXISTS idx_entity_files_file ON entity_files(file_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entity_rel_source ON entity_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_entity_rel_target ON entity_relations(target_id);
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
CREATE TABLE IF NOT EXISTS wiki_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '未分类',
    content_md TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    source_file_ids TEXT NOT NULL DEFAULT '[]',
    entity_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    compiled_by TEXT NOT NULL DEFAULT 'llm',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wiki_cat ON wiki_pages(category);
CREATE INDEX IF NOT EXISTS idx_wiki_status ON wiki_pages(status);
CREATE TABLE IF NOT EXISTS wiki_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_page_id INTEGER NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    to_page_id INTEGER NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'wiki',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_page_id, to_page_id, link_type)
);
CREATE TABLE IF NOT EXISTS wiki_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT 'llm',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dms_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER,
    dms_doc_id INTEGER NOT NULL,
    dms_version INTEGER NOT NULL DEFAULT 0,
    dms_folder_path TEXT NOT NULL DEFAULT '',
    dms_name TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'imported',
    UNIQUE(dms_doc_id)
);
CREATE INDEX IF NOT EXISTS idx_dms_imports_file ON dms_imports(file_id);
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
    created_at REAL NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    next_retry_at REAL NOT NULL DEFAULT 0,
    dead_letter INTEGER NOT NULL DEFAULT 0,
    checkpoint_stage TEXT NOT NULL DEFAULT '',
    checkpoint_data TEXT NOT NULL DEFAULT '{}'
);

-- C1: 文件权限表
CREATE TABLE IF NOT EXISTS file_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    user_id INTEGER,
    role TEXT,
    permission TEXT NOT NULL DEFAULT 'read',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fp_file ON file_permissions(file_id);
CREATE INDEX IF NOT EXISTS idx_fp_user ON file_permissions(user_id);

-- W4: 语义缓存表
CREATE TABLE IF NOT EXISTS semantic_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    embedding BLOB,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_hit_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sc_hash ON semantic_cache(query_hash);

-- S8: Rerank 缓存表
-- 缺陷A 修复（2026-09-21）：此表曾与 rerank.py 的惰性建表定义冲突
--   （旧：fingerprint/query/results 无主键；新：q/top_k/fp/results_json 复合主键），
--   导致 rerank.py 的 CREATE TABLE IF NOT EXISTS 静默 no-op、所有读写报错被吞，
--   持久缓存从未生效（实测 0 行）。现以此处为唯一权威定义，与 rerank.py 代码对齐。
CREATE TABLE IF NOT EXISTS rerank_cache (
    q TEXT NOT NULL,
    top_k INTEGER NOT NULL,
    fp TEXT NOT NULL,
    results_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (q, top_k, fp)
);

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT NOT NULL DEFAULT '',
    target_type TEXT,
    target_id INTEGER,
    detail TEXT,
    ip TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
"""


def _new_conn() -> sqlite3.Connection:
    return make_conn(DB_PATH)


def make_conn(db_path: str) -> sqlite3.Connection:
    """#3（2026-09-21）：统一 SQLite 连接工厂。

    供除主库 rag.db 外的其他 SQLite 库（如 plugins.db）复用同一套 PRAGMA 配置
    （WAL + foreign_keys + busy_timeout），避免各处自建连接导致口径不一。

    注意：本函数只负责「建连接 + 配置 PRAGMA」，不负责 thread-local / contextvar
    缓存。调用方自行管理连接生命周期（建议配合 close_thread_conn 语义）。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT}")
    return conn


def _get_conn() -> sqlite3.Connection:
    """获取当前上下文的连接。

    连接获取优先级（消除双层歧义的统一约定）：
      1. contextvars（`set_conn` 注入）：用于 FastAPI 请求生命周期——每个 HTTP 请求
         由 server.py 的 db_connection_per_request 中间件新建连接并 set_conn，请求结束
         关闭。这保证 async 协程下连接不跨请求串用。
      2. threading.local（`_local.conn`）：用于无 contextvars 的后台线程（入库 Stage、
         插件子进程、定时任务等），按线程隔离。

    两者选一的判据是「是否在请求上下文内」，而非线程——这样后台线程即便被 uvicorn
    线程池调度也不会误拿请求连接。
    """
    conn = _conn_var.get()
    if conn is not None:
        return conn
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _new_conn()
    return _local.conn


# 公开别名（消除下划线私有函数被外部 import 的尴尬）
get_connection = _get_conn


def set_conn(conn: sqlite3.Connection):
    _conn_var.set(conn)


def reset_conn():
    """清除当前上下文的连接标记（请求结束时由中间件调用，避免 contextvar 残留串用）"""
    _conn_var.set(None)


def close_thread_conn():
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def init_db():
    conn = _get_conn()
    conn.executescript(SCHEMA)
    _migrate_add_column(conn, "files", "summary", "TEXT NOT NULL DEFAULT ''")
    _migrate_add_column(conn, "files", "folder", "TEXT NOT NULL DEFAULT '/'")
    _migrate_add_column(conn, "files", "doc_kind", "TEXT NOT NULL DEFAULT '未分类'")
    _migrate_add_column(conn, "files", "authority", "INTEGER NOT NULL DEFAULT 0")
    # P20: 任务队列持久化 + 重试
    _migrate_add_column(conn, "tasks", "retry_count", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "tasks", "max_retries", "INTEGER NOT NULL DEFAULT 3")
    _migrate_add_column(conn, "tasks", "next_retry_at", "REAL NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "tasks", "dead_letter", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "tasks", "checkpoint_stage", "TEXT NOT NULL DEFAULT ''")
    _migrate_add_column(conn, "tasks", "checkpoint_data", "TEXT NOT NULL DEFAULT '{}'")
    # P3: 清洗质量分
    _migrate_add_column(conn, "files", "quality_score", "INTEGER NOT NULL DEFAULT -1")  # -1=未评分, 0-100=质量分
    # P22: 上传去重（content hash）
    _migrate_add_column(conn, "files", "content_hash", "TEXT DEFAULT NULL")
    # P23: 回收站（软删除）
    _migrate_add_column(conn, "files", "deleted_at", "TEXT DEFAULT NULL")
    # 缺陷A 修复：重建历史遗留的 rerank_cache 旧结构（fingerprint/query/results）
    _repair_rerank_cache(conn)
    # #1 迁移收敛：自动应用 migrations/ 下待执行的版本化迁移并维护 schema_version
    _apply_pending_migrations(conn)
    conn.commit()


def _apply_pending_migrations(conn):
    """#1（2026-09-21）：让 init_db 与 migrations/ 版本框架收敛为一条通道。

    背景：项目存在两套 schema 演进机制——init_db 的内联 SCHEMA + _migrate_add_column，
    与 migrations/ + scripts/migrate.py（带 schema_version 表），init_db 不维护
    schema_version，两套会漂移。

    本函数在启动时自动执行 pending 迁移并回写 schema_version，使「启动即同步」。
    与 scripts/migrate.py 逻辑一致（up 函数 / UP_SQL 两种形式），幂等可重复执行。
    迁移目录不存在或无 pending 时静默返回（不阻塞启动）。
    """
    import importlib.util
    from pathlib import Path
    mig_dir = Path(__file__).parent.parent.parent / "migrations"
    if not mig_dir.exists():
        return
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] if row and row[0] is not None else 0
        pending = []
        for f in sorted(mig_dir.glob("*.py")):
            parts = f.stem.split("_", 1)
            if len(parts) >= 2 and parts[0].isdigit() and int(parts[0]) > current:
                pending.append((int(parts[0]), parts[1], f))
        if not pending:
            return
        for version, name, path in sorted(pending, key=lambda x: x[0]):
            try:
                spec = importlib.util.spec_from_file_location(f"mig_{version}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "up"):
                    mod.up(conn)
                elif hasattr(mod, "UP"):
                    conn.executescript(mod.UP)
                elif hasattr(mod, "UP_SQL"):
                    conn.executescript(mod.UP_SQL)
                else:
                    logger.warning(f"迁移 {version:03d} {name} 无 up/UP/UP_SQL，跳过")
                    continue
                conn.execute(
                    "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.commit()
                logger.warning(f"迁移：已应用 {version:03d} {name}")
            except Exception as e:
                logger.error(f"迁移 {version:03d} {name} 失败：{e}")
                conn.rollback()
    except Exception as e:
        logger.error(f"自动迁移执行失败（不阻塞启动）：{e}")


def _repair_rerank_cache(conn):
    """缺陷A（2026-09-21）：旧版 rerank_cache 结构（fingerprint/query/results）与
    rerank.py 代码使用的（q/top_k/fp/results_json）不一致，导致持久缓存静默失效。

    旧表无有效数据（缓存可重建），检测到旧结构直接 DROP 重建为新结构。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(rerank_cache)").fetchall()}
    except Exception:
        return
    if not cols:
        return  # 表不存在，SCHEMA 已建好
    if "results_json" in cols:
        return  # 已是新结构
    conn.execute("DROP TABLE IF EXISTS rerank_cache")
    conn.execute("""
        CREATE TABLE rerank_cache (
            q TEXT NOT NULL,
            top_k INTEGER NOT NULL,
            fp TEXT NOT NULL,
            results_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (q, top_k, fp)
        )
    """)
    logger.warning("迁移：rerank_cache 旧结构已重建为新结构（缺陷A 修复）")


def _migrate_add_column(conn, table: str, column: str, ddl: str):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.warning(f"迁移：{table} 表新增列 {column}")
