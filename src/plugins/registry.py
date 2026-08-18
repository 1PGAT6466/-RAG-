"""
registry.py — 插件注册表（SQLite 持久化）

职责：记录插件身份、版本、状态、manifest，提供查询/写入。
对应设计文档 §4 最小内核组件之一。
"""
import json
import sqlite3
import threading
from pathlib import Path

from config import DATA_DIR

_DB_PATH = str(DATA_DIR / "plugins.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS plugins (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    api_version TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'tool',
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'installed',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    installed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def register(manifest: dict) -> None:
    """写入/更新插件记录（不改变 status）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO plugins (name, version, api_version, kind, display_name,
                             description, author, status, manifest_json)
        VALUES (?,?,?,?,?,?,?, 'installed', ?)
        ON CONFLICT(name) DO UPDATE SET
            version=excluded.version,
            api_version=excluded.api_version,
            kind=excluded.kind,
            display_name=excluded.display_name,
            description=excluded.description,
            author=excluded.author,
            manifest_json=excluded.manifest_json,
            updated_at=datetime('now','localtime')
        """,
        (
            manifest.get("name", ""),
            manifest.get("version", ""),
            manifest.get("api_version", ""),
            manifest.get("kind", "tool"),
            manifest.get("display_name", manifest.get("name", "")),
            manifest.get("description", ""),
            manifest.get("author", ""),
            json.dumps(manifest, ensure_ascii=False),
        ),
    )
    conn.commit()


def get(name: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM plugins WHERE name=?", (name,)).fetchone()
    if not row:
        return None
    return dict(row)


def get_manifest(name: str) -> dict | None:
    row = get(name)
    if not row:
        return None
    try:
        return json.loads(row["manifest_json"])
    except (json.JSONDecodeError, TypeError):
        return None


def list_all() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM plugins ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def set_status(name: str, status: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE plugins SET status=?, updated_at=datetime('now','localtime') WHERE name=?",
        (status, name),
    )
    conn.commit()


def remove(name: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM plugins WHERE name=?", (name,))
    conn.commit()
