"""Migration 003: Audit log table for tracking user actions (Phase 2 audit)."""
import sqlite3

def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            detail TEXT,
            ip TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id)")

def down(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS audit_log")
