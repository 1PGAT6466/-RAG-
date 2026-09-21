import sqlite3
conn = sqlite3.connect("E:/更新RAG框架/data/rag.db")
conn.row_factory = sqlite3.Row

fts_count = conn.execute("SELECT count(*) as c FROM chunks_fts").fetchone()["c"]
chunks_count = conn.execute("SELECT count(*) as c FROM chunks").fetchone()["c"]
print(f"chunks: {chunks_count}, chunks_fts: {fts_count}")

# FTS search tests
for q in ["连接器", "供应商", "Activar", "伺服压装"]:
    try:
        r = conn.execute("SELECT count(*) as c FROM chunks_fts WHERE chunks_fts MATCH ?", (q,)).fetchone()["c"]
        print(f"FTS match '{q}': {r}")
    except Exception as e:
        print(f"FTS match '{q}': ERROR {e}")

# Sample FTS content
sample = conn.execute("SELECT rowid, content FROM chunks_fts LIMIT 3").fetchall()
for s in sample:
    print(f"  rowid={s['rowid']} content={s['content'][:100]}")

conn.close()
