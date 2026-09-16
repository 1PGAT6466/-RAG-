"""文件权限操作（Phase 3 文件级权限）"""
import logging
from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.permissions")


def set_file_permission(file_id: int, user_id: int = None, role: str = None, permission: str = "read"):
    """设置文件权限（user_id 或 role 二选一）"""
    conn = _get_conn()
    # 去重：同文件+同用户/角色只保留一条（显式事务包裹，避免 DELETE+INSERT 竞态）
    with conn:
        if user_id:
            conn.execute("DELETE FROM file_permissions WHERE file_id=? AND user_id=?", (file_id, user_id))
        elif role:
            conn.execute("DELETE FROM file_permissions WHERE file_id=? AND role=?", (file_id, role))
        conn.execute(
            "INSERT INTO file_permissions (file_id, user_id, role, permission) VALUES (?,?,?,?)",
            (file_id, user_id, role, permission)
        )


def get_file_permissions(file_id: int) -> list[dict]:
    """获取文件的所有权限"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM file_permissions WHERE file_id=?", (file_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_accessible_file_ids(user_id: int, roles: list[str] = None) -> set[int] | None:
    """获取用户可访问的文件 ID 集合。

    返回 None 表示无限制（管理员或无权限配置），
    返回 set[int] 表示只能访问这些文件。
    """
    conn = _get_conn()

    # 管理员无限制
    user = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if user and user["role"] == "admin":
        return None

    # 检查是否有任何文件配置了权限
    count = conn.execute("SELECT COUNT(*) FROM file_permissions").fetchone()[0]
    if count == 0:
        return None  # 无权限配置，所有文件可访问

    # 收集用户可访问的文件
    accessible = set()

    # 直接授予用户的权限
    rows = conn.execute(
        "SELECT file_id FROM file_permissions WHERE user_id=?", (user_id,)
    ).fetchall()
    for r in rows:
        accessible.add(r["file_id"])

    # 通过角色授予的权限
    if roles:
        for role in roles:
            rows = conn.execute(
                "SELECT file_id FROM file_permissions WHERE role=?", (role,)
            ).fetchall()
            for r in rows:
                accessible.add(r["file_id"])

    # 无任何权限配置的文件（默认公开）
    perm_file_ids = conn.execute("SELECT DISTINCT file_id FROM file_permissions").fetchall()
    perm_ids = {r["file_id"] for r in perm_file_ids}
    all_file_ids = conn.execute("SELECT id FROM files WHERE deleted_at IS NULL").fetchall()
    public_ids = {r["id"] for r in all_file_ids} - perm_ids

    return accessible | public_ids


def delete_file_permission(perm_id: int):
    """删除权限"""
    conn = _get_conn()
    conn.execute("DELETE FROM file_permissions WHERE id=?", (perm_id,))
    conn.commit()
