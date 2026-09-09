"""
插件 API 路由 — /api/plugins
====================
提供插件的列表、启停、卸载、调用、状态查询，供前端插件中心使用。

使用 register(router) 方式注册，避免新版 FastAPI 子 router include 的懒加载问题。
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from src.auth.deps import get_current_user, require_admin
from src.plugins import (
    list_plugins, invoke, registry, lifecycle,
)

logger = logging.getLogger("rag.api.plugins")


class InvokeReq(BaseModel):
    method: str
    params: dict = {}


def register(router: APIRouter):
    """把插件端点注册到给定的 router 上"""

    @router.get("/api/plugins")
    async def api_list_plugins(user=Depends(get_current_user)):
        try:
            plugins = list_plugins()
            return {"status": "ok", "data": plugins}
        except Exception as e:
            logger.error(f"插件列表失败: {e}")
            raise HTTPException(500, f"插件列表失败: {e}")

    @router.get("/api/plugins/{name}")
    async def api_get_plugin(name: str, user=Depends(get_current_user)):
        row = registry.get(name)
        if not row:
            raise HTTPException(404, f"插件不存在: {name}")
        data = dict(row)
        data["manifest"] = registry.get_manifest(name)
        return {"status": "ok", "data": data}

    @router.post("/api/plugins/{name}/enable")
    async def api_enable(name: str, user=Depends(require_admin)):
        try:
            row = lifecycle.enable(name)
            return {"status": "ok", "data": row}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/plugins/{name}/disable")
    async def api_disable(name: str, user=Depends(require_admin)):
        try:
            row = lifecycle.disable(name)
            return {"status": "ok", "data": row}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/plugins/{name}/uninstall")
    async def api_uninstall(name: str, user=Depends(require_admin)):
        try:
            lifecycle.uninstall(name)
            return {"status": "ok"}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/plugins/{name}/invoke")
    async def api_invoke(name: str, req: InvokeReq, user=Depends(require_admin)):
        result = invoke(name, req.method, req.params)
        if "error" in result:
            raise HTTPException(400, result["error"])
        return {"status": "ok", "data": result.get("result")}

    @router.get("/api/plugins/{name}/status")
    async def api_status(name: str, user=Depends(get_current_user)):
        row = registry.get(name)
        if not row:
            raise HTTPException(404, f"插件不存在: {name}")
        return {"status": "ok", "data": {"name": name, "status": row["status"]}}
