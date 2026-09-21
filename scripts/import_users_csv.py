"""从 CSV 文件批量导入用户

CSV 格式（第一行为表头）：
username,password,role
zhangsan,123456,user
lisi,123456,admin

用法：python scripts\import_users_csv.py users.csv
"""
import csv
import sys
import sqlite3
import bcrypt

DB = r'E:\更新RAG框架\data\rag.db'

if len(sys.argv) < 2:
    print("用法: python import_users_csv.py <csv文件路径>")
    sys.exit(1)

csv_path = sys.argv[1]
conn = sqlite3.connect(DB)
cur = conn.cursor()

with open(csv_path, encoding='utf-8') as f:
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        username = row['username'].strip()
        password = row['password'].strip()
        role = row.get('role', 'user').strip() or 'user'

        if cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            print(f"跳过 {username}（已存在）")
            continue

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                    (username, hashed, role))
        print(f"✓ {username}（{role}）")
        count += 1

conn.commit()
conn.close()
print(f"\n完成！共导入 {count} 个用户")
