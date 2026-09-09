"""
存储层 — SQLite 连接管理 + Schema + 迁移
========================================
职责：连接池、Schema 定义、初始化迁移。
具体 CRUD 操作已拆分到同包模块：
  - files.py         文件/文件夹/图片
  - chunks.py        Chunk + FTS5 搜索 + 链接
  - entities.py      实体/关系/图谱
  - conversations.py 对话会话
  - tasks.py         入库任务持久化
"""
import sqlite3
import json
import threading
import logging
import contextvars
from config import DB_PATH

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
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_tri USING fts5(content, tokenize='trigram');
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
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'knowledge',
    sources TEXT NOT NULL DEFAULT '[]',
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
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_conn() -> sqlite3.Connection:
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
    conn.commit()


def _migrate_add_column(conn, table: str, column: str, ddl: str):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.warning(f"迁移：{table} 表新增列 {column}")


# === 向后兼容 re-export ===
# 所有外部调用者仍可 from src.storage.db import xxx
from src.storage.files import (
    add_file, get_file, list_files, list_files_with_entities,
    update_file_category, update_file_tags, update_file_summary, update_file_folder,
    normalize_folder, list_folders, delete_file, sync_chunk_count,
    add_images, list_images, count_images,
)
from src.storage.chunks import (
    add_chunks_batch, get_chunks_by_file, fts_search, add_link, get_graph_data, get_file_backlinks,
)
from src.storage.entities import (
    upsert_entity, add_entity_chunk, add_entity_file, add_entity_relation,
    list_entities, get_entity, get_entity_by_name,
    get_entity_chunks, get_entity_files,
    get_standard_category, set_standard_category,
    get_entity_spec_params, get_entity_relations, get_entity_graph,
)
from src.storage.conversations import (
    create_conversation, list_conversations, get_conversation,
    get_conversation_messages, add_conversation_message,
    update_conversation_title, delete_conversation,
)
from src.storage.tasks import (
    save_task, load_tasks, mark_stale_tasks_failed, delete_task,
)
