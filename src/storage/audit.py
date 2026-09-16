"""审计日志操作（Phase 2 审计表）"""
import logging
from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.audit")


def log_action(user_id: int = None, username: str = None, action: str = "",
               target_type: str = None, target_id: int = None,
               detail: str = None, ip: str = None):
    """记录一条审计日志"""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO audit_log (user_id, username, action, target_type, target_id, detail, ip) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, username, action, target_type, target_id, detail, ip)
    )
    conn.commit()


def get_audit_logs(limit: int = 100, action: str = None,
                   user_id: int = None, target_type: str = None) -> list[dict]:
    """查询审计日志"""
    conn = _get_conn()
    conds = []
    args = []
    if action:
        conds.append("action = ?")
        args.append(action)
    if user_id:
        conds.append("user_id = ?")
        args.append(user_id)
    if target_type:
        conds.append("target_type = ?")
        args.append(target_type)
    where = " WHERE " + " AND ".join(conds) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM audit_log{where} ORDER BY created_at DESC LIMIT ?",
        tuple(args) + (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
