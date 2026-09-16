"""Migration 002: Add content_hash for dedup and deleted_at for soft delete.

P22: content_hash (sha256) column for upload dedup
P23: deleted_at column for recycle bin (soft delete)
"""
import sqlite3

UP_SQL = """
-- P22: Add content hash for dedup
ALTER TABLE files ADD COLUMN content_hash TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files(content_hash) WHERE content_hash IS NOT NULL;

-- P23: Add soft delete column
ALTER TABLE files ADD COLUMN deleted_at TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_files_deleted_at ON files(deleted_at) WHERE deleted_at IS NOT NULL;
"""

DOWN_SQL = """
-- SQLite doesn't support DROP COLUMN before 3.35.0
-- In production, you'd need to recreate the table
"""

def up(conn: sqlite3.Connection):
    conn.executescript(UP_SQL)

def down(conn: sqlite3.Connection):
    conn.executescript(DOWN_SQL)
