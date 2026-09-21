"""查看数据库完整信息：表结构 + 行数 + 关键字段示例"""
import sqlite3
import os

DB = r'E:\更新RAG框架\data\rag.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

# 获取所有表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]

print("=" * 60)
print("数据库: %s" % DB)
print("大小: %.1f MB" % (os.path.getsize(DB) / 1024 / 1024))
print("表数量: %d" % len(tables))
print("=" * 60)

for tbl in tables:
    # 跳过 FTS 内部表
    if tbl.startswith('chunks_fts') or tbl.startswith('sqlite_'):
        continue

    cur.execute("PRAGMA table_info(%s)" % tbl)
    cols = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM %s" % tbl)
    count = cur.fetchone()[0]

    print("\n--- %s (%d 行) ---" % (tbl, count))
    for c in cols:
        nn = 'NOT NULL' if c[3] else ''
        default = ' DEFAULT %s' % c[4] if c[4] is not None else ''
        pk = ' [PK]' if c[5] else ''
        print("  %-25s %-12s %s%s%s" % (c[1], c[2], nn, default, pk))

    # 前 3 行示例（不显示敏感字段）
    if count > 0:
        safe_cols = [c[1] for c in cols if 'password' not in c[1].lower() and 'token' not in c[1].lower() and 'hash' not in c[1].lower()]
        if safe_cols:
            col_str = ', '.join(safe_cols[:8])  # 最多 8 列
            cur.execute("SELECT %s FROM %s LIMIT 3" % (col_str, tbl))
            rows = cur.fetchall()
            for row in rows:
                vals = [str(v)[:40] if v is not None else 'NULL' for v in row]
                print("  示例: %s" % ' | '.join(vals))

print("\n" + "=" * 60)
print("索引:")
cur.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY tbl_name, name")
for r in cur.fetchall():
    print("  %-40s -> %s" % (r[0], r[1]))

conn.close()
