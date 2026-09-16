"""
认证依赖 — get_current_user（单一职责） + require_admin（权限控制）
"""
from fastapi import HTTPException, Request, Depends

from src.auth.jwt import verify_token


async def get_current_user(request: Request):
    """从 Authorization: Bearer <token> 或 ?token=xxx 解析当前用户，失败抛 401。
    后者供 iframe 等无法设 Authorization header 的场景。"""
    auth = request.headers.get("Authorization", "")
    raw_token = None
    if auth.startswith("Bearer "):
        raw_token = auth[7:]
    if not raw_token:
        raw_token = request.query_params.get("token")
    if not raw_token:
        raise HTTPException(401, "未登录")
    try:
        return verify_token(raw_token)
    except Exception as e:
        import jwt as _jwt
        if isinstance(e, (_jwt.InvalidTokenError, _jwt.ExpiredSignatureError)):
            raise HTTPException(401, "token 无效或已过期")
        raise HTTPException(500, "认证服务异常")


def require_admin(user=Depends(get_current_user)):
    """仅 admin 可访问的依赖，非 admin 抛 403。
    用于写操作端点（上传/删除/改分类/改标签/插件管理），实现用户/管理员权限隔离。
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "无权限：仅管理员可执行此操作")
    return user
