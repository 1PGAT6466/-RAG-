import sqlite3, bcrypt

DB = r'E:\更新RAG框架\data\rag.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute('SELECT id, username, password_hash FROM users')
rows = cur.fetchall()

plain = []
hashed = []
for r in rows:
    if r[2] and r[2].startswith('$2'):
        hashed.append(r[1])
    else:
        plain.append((r[0], r[1], r[2]))

print('已哈希: %d 个' % len(hashed))
print('未哈希: %d 个' % len(plain))

for uid, uname, pw in plain:
    if not pw:
        print('  跳过 %s（密码为空）' % uname)
        continue
    new_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    conn.execute('UPDATE users SET password_hash=? WHERE id=?', (new_hash, uid))
    print('  已哈希: %s' % uname)

conn.commit()
conn.close()
print('\n完成！')
