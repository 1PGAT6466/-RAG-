"""api/auth.py — 认证路由"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth.jwt import register, login, verify_token
from src.auth.deps import get_current_user
from src.auth.rate_limit import login_limiter, register_limiter

logger = logging.getLogger("rag.api.auth")
router = APIRouter()


class RegisterReq(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, pattern=r'^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$')
    password: str = Field(..., min_length=6, max_length=128)

class LoginReq(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/api/auth/register")
def api_register(req: RegisterReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not register_limiter.allow(client_ip):
        raise HTTPException(429, "注册过于频繁，请稍后再试")
    try:
        result = register(req.username, req.password)
        return {"status": "ok", "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/auth/login")
def api_login(req: LoginReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(client_ip):
        raise HTTPException(429, "尝试次数过多，请 1 分钟后再试")
    try:
        token = login(req.username, req.password)
        payload = verify_token(token)
        return {"status": "ok", "data": {"token": token, "username": req.username, "role": payload.get("role", "user")}}
    except ValueError as e:
        raise HTTPException(401, str(e))
