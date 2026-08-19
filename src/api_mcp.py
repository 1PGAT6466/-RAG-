"""
MCP API 路由 — /api/mcp
====================
提供 MCP 市场浏览、安装/卸载、工具列表/调用，供前端 MCP 市场使用。

使用 register(router) 方式注册（与 api_plugins 一致，避免 include 懒加载问题）。
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from src.auth.deps import get_current_user, require_admin
from src.mcp import (
    list_servers, get_server,
    list_installed, install_server, uninstall_server, get_installed,
    list_mcp_tools, call_mcp_tool,
)

logger = logging.getLogger("rag.api.mcp")


class McpInstallReq(BaseModel):
    qualifiedName: str
    command: str
    args: list[str] = []
    env: dict = {}


class McpUninstallReq(BaseModel):
    qualifiedName: str


class McpCallReq(BaseModel):
    qualifiedName: str
    tool: str
    arguments: dict = {}


def register(router: APIRouter):

    @router.get("/api/mcp/market")
    async def api_mcp_market(q: str = "", limit: int = 50, user=Depends(get_current_user)):
        """浏览 MCP 市场（Smithery registry）"""
        servers = await list_servers(query=q, limit=min(limit, 100))
        return {"status": "ok", "data": servers}

    @router.get("/api/mcp/installed")
    async def api_mcp_installed(user=Depends(get_current_user)):
        """已安装的 MCP server 列表"""
        return {"status": "ok", "data": list_installed()}

    @router.post("/api/mcp/install")
    async def api_mcp_install(req: McpInstallReq, user=Depends(require_admin)):
        """安装 MCP server（需管理员）"""
        # 从 market 拉详情补全 display_name/description
        display = req.qualifiedName
        desc = ""
        server_info = await get_server(req.qualifiedName)
        if server_info:
            display = server_info.get("displayName") or display
            desc = server_info.get("description") or ""
        install_server(req.qualifiedName, display, desc, req.command, req.args, req.env)
        return {"status": "ok", "data": {"qualifiedName": req.qualifiedName, "installed": True}}

    @router.post("/api/mcp/uninstall")
    async def api_mcp_uninstall(req: McpUninstallReq, user=Depends(require_admin)):
        """卸载 MCP server（需管理员）"""
        ok = uninstall_server(req.qualifiedName)
        if not ok:
            raise HTTPException(404, "未找到该 MCP server")
        return {"status": "ok", "data": {"qualifiedName": req.qualifiedName, "uninstalled": True}}

    @router.get("/api/mcp/{qualified_name:path}/tools")
    async def api_mcp_tools(qualified_name: str, user=Depends(get_current_user)):
        """列出某 MCP server 的工具（实际连接 server 获取）"""
        s = get_installed(qualified_name)
        if not s:
            raise HTTPException(404, "MCP server 未安装")
        try:
            tools = await list_mcp_tools(s["command"], args=s["args"], env=s["env"])
            return {"status": "ok", "data": tools}
        except Exception as e:
            raise HTTPException(502, f"连接 MCP server 失败: {str(e)}")

    @router.post("/api/mcp/{qualified_name:path}/call")
    async def api_mcp_call(qualified_name: str, req: McpCallReq, user=Depends(require_admin)):
        """调用 MCP server 的工具（需管理员）"""
        s = get_installed(qualified_name)
        if not s:
            raise HTTPException(404, "MCP server 未安装")
        result = await call_mcp_tool(s["command"], req.tool, req.arguments,
                                     args=s["args"], env=s["env"])
        return {"status": "ok", "data": result}
