import sqlite3
conn = sqlite3.connect(r'E:\更新RAG框架\data\rag.db')
cur = conn.cursor()
cur.execute('PRAGMA table_info(users)')
cols = cur.fetchall()
print('=== users 表字段 ===')
for c in cols:
    nn = 'NOT NULL' if c[3] else ''
    pk = 'PK' if c[5] else ''
    print('  %-20s %-10s %s %s' % (c[1], c[2], nn, pk))
cur.execute('SELECT COUNT(*) FROM users')
print('\n总用户数:', cur.fetchone()[0])
cur.execute('SELECT id, username, role, created_at FROM users')
for row in cur.fetchall():
    print('  id=%d  user=%s  role=%s  created=%s' % row)
conn.close()
