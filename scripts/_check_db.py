import sqlite3
conn = sqlite3.connect(r'E:\更新RAG框架\data\rag.db')
cur = conn.cursor()
# Check table structure
cur.execute("PRAGMA table_info(files)")
print("files columns:", [r[1] for r in cur.fetchall()])
cur.execute("SELECT COUNT(*) FROM files")
print('Total files:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM chunks")
print('Total chunks:', cur.fetchone()[0])
cur.execute("SELECT id, name, category FROM files LIMIT 15")
for r in cur.fetchall():
    print(f'  file {r[0]}: {r[1]} [{r[2]}]')
cur.execute("SELECT category, COUNT(*) FROM files GROUP BY category")
for r in cur.fetchall():
    print(f'  category: {r[0]} -> {r[1]} files')
conn.close()
