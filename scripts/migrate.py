"""Database migration framework for RAG system.

Usage:
  python scripts/migrate.py status     # Show current version and pending migrations
  python scripts/migrate.py upgrade    # Apply all pending migrations
  python scripts/migrate.py downgrade <version>  # Rollback to version (not implemented)

Migration files live in migrations/ directory, named like:
  001_initial_schema.py
  002_add_feedback_table.py

Each migration file must define:
  UP: str = "SQL to apply"  (or a function up(conn))
  DOWN: str = "SQL to rollback"  (or a function down(conn))
"""
import sqlite3
import sys
import time
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "rag.db"
MIGRATIONS_DIR = ROOT / "migrations"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_schema_version(conn):
    """Create schema_version table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()


def get_current_version(conn) -> int:
    """Get the latest applied migration version."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row[0] is not None else 0
    except:
        return 0


def discover_migrations() -> list[tuple[int, str, Path]]:
    """Find all migration files and return (version, name, path) sorted."""
    if not MIGRATIONS_DIR.exists():
        return []
    
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("*.py")):
        # Parse version from filename: 001_initial_schema.py -> 1
        parts = f.stem.split("_", 1)
        if len(parts) >= 2 and parts[0].isdigit():
            version = int(parts[0])
            name = parts[1]
            migrations.append((version, name, f))
    
    return sorted(migrations, key=lambda x: x[0])


def load_migration(path: Path):
    """Load a migration module."""
    spec = importlib.util.spec_from_file_location("migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_status():
    conn = get_conn()
    ensure_schema_version(conn)
    current = get_current_version(conn)
    migrations = discover_migrations()
    conn.close()

    print(f"Current version: {current}")
    print(f"Total migrations: {len(migrations)}")
    
    pending = [(v, n, p) for v, n, p in migrations if v > current]
    if pending:
        print(f"\nPending ({len(pending)}):")
        for v, name, path in pending:
            print(f"  {v:03d} {name}")
    else:
        print("\nAll migrations applied.")
    return 0


def cmd_upgrade():
    conn = get_conn()
    ensure_schema_version(conn)
    current = get_current_version(conn)
    migrations = discover_migrations()
    
    pending = [(v, n, p) for v, n, p in migrations if v > current]
    if not pending:
        print("Nothing to migrate.")
        conn.close()
        return 0
    
    print(f"Migrating from v{current} to v{pending[-1][0]}...")
    
    applied = 0
    for version, name, path in pending:
        print(f"  Applying {version:03d} {name}...", end=" ")
        try:
            mod = load_migration(path)
            if hasattr(mod, "up"):
                mod.up(conn)
            elif hasattr(mod, "UP"):
                conn.executescript(mod.UP)
            else:
                print("SKIP (no up/UP)")
                continue
            
            conn.execute(
                "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                (version, name)
            )
            conn.commit()
            applied += 1
            print("OK")
        except Exception as e:
            conn.rollback()
            print(f"FAIL: {e}")
            conn.close()
            return 1
    
    print(f"\nApplied {applied} migrations. Current version: {version}")
    conn.close()
    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate.py [status|upgrade]")
        return 1
    
    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status()
    elif cmd == "upgrade":
        return cmd_upgrade()
    else:
        print(f"Unknown command: {cmd}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
