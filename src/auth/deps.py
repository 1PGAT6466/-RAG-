"""
认证依赖 — get_current_user（单一职责） + require_admin（权限控制）
"""
from fastapi import HTTPException, Request, Depends

from src.auth.jwt import verify_token


async def get_current_user(request: Request):
    """从 Authorization: Bearer <token> 解析当前用户，失败抛 401。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        return verify_token(auth[7:])
    except Exception:
        raise HTTPException(401, "token 无效或已过期")


def require_admin(user=Depends(get_current_user)):
    """仅 admin 可访问的依赖，非 admin 抛 403。
    用于写操作端点（上传/删除/改分类/改标签/插件管理），实现用户/管理员权限隔离。
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "无权限：仅管理员可执行此操作")
    return user
