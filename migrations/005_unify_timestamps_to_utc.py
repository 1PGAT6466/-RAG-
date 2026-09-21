"""Migration 005: 统一时间戳口径为 UTC（#2，2026-09-21）

背景：历史上三套口径并存——
  - files/chunks/conversations 等多数表用 datetime('now','localtime')（本地时区）
  - wiki/images/standard_categories 曾用 datetime('now')（UTC）
  - tasks 用 REAL epoch

本迁移把「原本存的是本地时间」的列，转换成 UTC 存储（减去本地 UTC 偏移），
并在 SCHEMA 层把默认值统一为 datetime('now')（UTC）。API/前端负责本地化显示。

注意：只转换「确知是本地时间」的列，避免把本就正确的 UTC 数据再次偏移。
tasks.created_at 为 REAL epoch，语义与 UTC 无关，不做转换，仅保持原样。
"""
import sqlite3
import time


# 需要从「本地时间字符串」转成「UTC 时间字符串」的表.列
_LOCALTIME_COLUMNS = [
    ("files", "created_at"), ("files", "updated_at"),
    ("chunks", "created_at"),
    ("links", "created_at"),
    ("users", "created_at"),
    ("entities", "created_at"),
    ("conversations", "created_at"), ("conversations", "updated_at"),
    ("conversation_messages", "created_at"),
    ("feedback", "created_at"),
    ("wiki_pages", "created_at"), ("wiki_pages", "updated_at"),
    ("wiki_links", "created_at"),
    ("wiki_versions", "created_at"),
    ("dms_imports", "imported_at"),
    ("file_permissions", "created_at"),
    ("semantic_cache", "created_at"),
    ("audit_log", "created_at"),
    ("rerank_cache", "created_at"),
]


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row)


def _has_column(conn, table: str, col: str) -> bool:
    try:
        return col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return False


def up(conn: sqlite3.Connection):
    """把存量本地时间字符串转为 UTC 字符串。

    time.timezone = 本地相对 UTC 的偏移（西为正；东八区 = -28800）。
    本地 = UTC + 8h → UTC = 本地 - 8h，即用 time.timezone 本身的秒数作修饰符。
    """
    offset_seconds = time.timezone if not time.localtime().tm_isdst else time.altzone
    converted = 0
    for table, col in _LOCALTIME_COLUMNS:
        if not _table_exists(conn, table) or not _has_column(conn, table, col):
            continue
        try:
            cur = conn.execute(
                f"UPDATE {table} "
                f"SET {col} = datetime({col}, ?) "
                f"WHERE {col} IS NOT NULL AND {col} != ''",
                (f"{offset_seconds} seconds",),
            )
            converted += cur.rowcount or 0
        except sqlite3.Error:
            # 单表失败不影响其它表；交由上层提交策略决定
            continue
    conn.commit()
    return converted


def down(conn: sqlite3.Connection):
    """回滚：把 UTC 字符串加回本地偏移。"""
    offset_seconds = time.timezone if not time.localtime().tm_isdst else time.altzone
    for table, col in _LOCALTIME_COLUMNS:
        if not _table_exists(conn, table) or not _has_column(conn, table, col):
            continue
        try:
            conn.execute(
                f"UPDATE {table} "
                f"SET {col} = datetime({col}, ?) "
                f"WHERE {col} IS NOT NULL AND {col} != ''",
                (f"{-offset_seconds} seconds",),
            )
        except sqlite3.Error:
            continue
    conn.commit()
