"""批量创建用户 — 每行一个 用户名,密码,角色

用法：python batch_add_users.py
"""
import sys
import os
from pathlib import Path

# 基于脚本自身路径定位项目根目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(_PROJECT_ROOT)

import sqlite3
import bcrypt
from config import DB_PATH

# ===== 在这里添加用户 =====
# 格式：(用户名, 密码, 角色)
# 角色：admin=管理员, user=普通用户
USERS = [
    ("zhangsan",  "123456",   "user"),
    ("lisi",      "123456",   "user"),
    ("wangwu",    "123456",   "admin"),
    # 继续添加...
]
# ==========================

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

for username, password, role in USERS:
    if cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        print(f"跳过 {username}（已存在）")
        continue

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username, hashed, role))
    print(f"✓ {username}（{role}）")

conn.commit()
conn.close()
print("\n完成！")
