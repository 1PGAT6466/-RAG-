"""用户管理脚本 — 直接操作 rag.db users 表"""
import sqlite3
import sys
import bcrypt

DB = r'E:\更新RAG框架\data\rag.db'


def list_users():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
    rows = cur.fetchall()
    print("=== 用户列表 (%d) ===" % len(rows))
    print("%-5s %-20s %-10s %s" % ("ID", "用户名", "角色", "创建时间"))
    print("-" * 60)
    for r in rows:
        print("%-5d %-20s %-10s %s" % r)
    conn.close()


def add_user(username: str, password: str, role: str = "user"):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    if cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        print("错误: 用户名 '%s' 已存在" % username)
        conn.close()
        return
    pwh = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username, pwh, role))
    conn.commit()
    print("✓ 创建成功: %s (id=%d, role=%s)" % (username, cur.lastrowid, role))
    conn.close()


def delete_user(username: str):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        print("错误: 用户 '%s' 不存在" % username)
        conn.close()
        return
    cur.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    print("✓ 已删除: %s" % username)
    conn.close()


def reset_password(username: str, new_password: str):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        print("错误: 用户 '%s' 不存在" % username)
        conn.close()
        return
    pwh = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    cur.execute("UPDATE users SET password_hash=? WHERE username=?", (pwh, username))
    conn.commit()
    print("✓ 密码已重置: %s" % username)
    conn.close()


def change_role(username: str, new_role: str):
    if new_role not in ("user", "admin"):
        print("错误: role 只能是 user 或 admin")
        return
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role=? WHERE username=?", (new_role, username))
    if cur.rowcount:
        conn.commit()
        print("✓ %s 角色已改为: %s" % (username, new_role))
    else:
        print("错误: 用户 '%s' 不存在" % username)
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python user_manage.py list                    # 列出所有用户")
        print("  python user_manage.py add <用户名> <密码> [role]  # 添加用户")
        print("  python user_manage.py del <用户名>            # 删除用户")
        print("  python user_manage.py passwd <用户名> <新密码>    # 重置密码")
        print("  python user_manage.py role <用户名> <user|admin>  # 修改角色")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "list":
        list_users()
    elif cmd == "add" and len(sys.argv) >= 4:
        role = sys.argv[4] if len(sys.argv) >= 5 else "user"
        add_user(sys.argv[2], sys.argv[3], role)
    elif cmd == "del" and len(sys.argv) >= 3:
        delete_user(sys.argv[2])
    elif cmd == "passwd" and len(sys.argv) >= 4:
        reset_password(sys.argv[2], sys.argv[3])
    elif cmd == "role" and len(sys.argv) >= 4:
        change_role(sys.argv[2], sys.argv[3])
    else:
        print("参数错误，查看用法: python user_manage.py")
