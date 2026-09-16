"""Migration 004: File-level permissions table (Phase 3).

Maps files to allowed users/roles for access control.
"""
import sqlite3

def up(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            user_id INTEGER,
            role TEXT,
            permission TEXT NOT NULL DEFAULT 'read',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fp_file ON file_permissions(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fp_user ON file_permissions(user_id)")

def down(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS file_permissions")
