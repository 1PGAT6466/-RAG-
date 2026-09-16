"""Migration 001: Initial schema baseline.

This migration captures the current state of the RAG database as the baseline.
It doesn't change anything - it just records that we're at version 1.
"""
import sqlite3

def up(conn: sqlite3.Connection):
    """Record current schema as baseline - no DDL changes needed."""
    # All tables already exist. This migration just establishes the version baseline.
    # Future migrations will add/alter tables incrementally.
    pass

def down(conn: sqlite3.Connection):
    """No rollback needed for baseline."""
    pass
