"""
认证 — JWT 登录/注册

密码哈希：bcrypt（慢哈希，抗暴力破解）
旧 sha256 哈希保留兼容（验证通过后透明升级为 bcrypt，平滑迁移不清库）
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
from config import JWT_SECRET, JWT_EXPIRY_HOURS
from src.storage.db import _get_conn

ALGORITHM = "HS256"

# bcrypt 哈希前缀标记（便于识别格式）
_BCRYPT_PREFIX = "$2"


def hash_password(password: str) -> str:
    """bcrypt 慢哈希（安全硬伤修复：原 sha256 快速哈希可被 GPU 暴力破解）"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证密码：支持 bcrypt（新）与旧 sha256 哈希（兼容，验证通过后由调用方升级）"""
    if hashed.startswith(_BCRYPT_PREFIX):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except ValueError:
            return False
    # 旧 sha256 格式 "salt:hexdigest"（兼容）
    try:
        salt, h = hashed.split(":", 1)
        expected = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return hmac.compare_digest(h, expected)
    except ValueError:
        return False


def _maybe_upgrade_hash(username: str, password: str, current_hash: str) -> None:
    """旧 sha256 哈希验证通过后，透明升级为 bcrypt（一次性平滑迁移）"""
    if current_hash.startswith(_BCRYPT_PREFIX):
        return
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE username=?",
        (hash_password(password), username),
    )
    conn.commit()


def register(username: str, password: str) -> dict:
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()
    if existing:
        raise ValueError("用户名已存在")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")

    pwh = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?,?)",
        (username, pwh)
    )
    conn.commit()
    return {"user_id": cur.lastrowid, "username": username}


def login(username: str, password: str) -> str:
    """返回 JWT token（payload 含 role，供前端角色分流 + 后端权限校验）"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username=?",
        (username,)
    ).fetchone()
    if not row:
        raise ValueError("用户名或密码错误")
    if not verify_password(password, row["password_hash"]):
        raise ValueError("用户名或密码错误")

    # 旧 sha256 哈希透明升级为 bcrypt
    _maybe_upgrade_hash(row["username"], password, row["password_hash"])

    payload = {
        "sub": row["username"],
        "user_id": row["id"],
        "role": row["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """验证 token，返回 payload"""
    return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
