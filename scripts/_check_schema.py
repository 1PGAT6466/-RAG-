"""Quick script to list database tables and check schema version."""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "rag.db"
conn = sqlite3.connect(str(DB))

# List tables
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:")
for t in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]
    print(f"  {t[0]}: {count} rows")

# Check schema_version
try:
    ver = conn.execute("SELECT * FROM schema_version").fetchall()
    print(f"\nschema_version: {ver}")
except:
    print("\nschema_version: does not exist")

conn.close()
