"""
MCP API 路由 — /api/mcp
====================
提供 MCP 市场浏览、安装/卸载、工具列表/调用，供前端 MCP 市场使用。

使用 register(router) 方式注册（与 api_plugins 一致，避免 include 懒加载问题）。
"""
import logging
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from src.api.errors import BizError, ErrorCode
from src.auth.deps import get_current_user, require_admin
from src.mcp import (
    list_servers, get_server, get_server_full, resolve_mcp_url,
    list_installed, install_server, uninstall_server, get_installed,
    list_mcp_tools_http, call_mcp_tool_http,
)
from src.mcp.client import list_mcp_tools as _list_mcp_tools_sync, call_mcp_tool as _call_mcp_tool_sync
from src.mcp.manager import _ensure_table
import sqlite3
import re
import os as _os

logger = logging.getLogger("rag.api.mcp")


def _fix_corrupted_args(qualified_name: str):
    """修复 SQLite 中可能被损坏的中文路径（Windows 编码问题）"""
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        row = conn.execute("SELECT args FROM mcp_servers WHERE qualified_name=?", (qualified_name,)).fetchone()
        if not row:
            return
        args_str = row[0]
        # 检测 replacement character（U+FFFD）或问号乱码
        if '\ufffd' not in args_str and '??' not in args_str:
            return
        # 尝试用已知的正确路径替换
        known_paths = {
            'E:\\更新RAG框架': _os.path.normpath(_os.path.join(_os.path.dirname(__file__), '..')).replace('/', '\\'),
        }
        args = json.loads(args_str)
        fixed = False
        for i, arg in enumerate(args):
            if isinstance(arg, str):
                for bad, good in known_paths.items():
                    if '\ufffd' in arg or '??' in arg:
                        # 用正则替换损坏的路径段
                        new_arg = re.sub(r'[A-Z]:\\[?\ufffd]+', good[:3], arg)
                        if new_arg != arg:
                            args[i] = new_arg
                            fixed = True
                            logger.info(f"修复 MCP args[{i}]: {arg} -> {new_arg}")
        if fixed:
            conn.execute("UPDATE mcp_servers SET args=? WHERE qualified_name=?",
                         (json.dumps(args, ensure_ascii=False), qualified_name))
            conn.commit()
    except Exception as e:
        logger.warning(f"修复 MCP args 失败: {e}")


class McpInstallReq(BaseModel):
    qualifiedName: str
    command: str = ""
    args: list[str] = []
    env: dict = {}
    transport: str = "auto"  # auto | http | stdio
    url: str = ""
    headers: dict = {}


class McpUninstallReq(BaseModel):
    qualifiedName: str


class McpCallReq(BaseModel):
    qualifiedName: str
    tool: str
    arguments: dict = {}


def register(router: APIRouter):

    @router.get("/api/mcp/market")
    async def api_mcp_market(q: str = "", page: int = 1, page_size: int = 20, user=Depends(get_current_user)):
        """浏览 MCP 市场（Smithery registry，支持服务端搜索 + 分页）"""
        data = await list_servers(query=q, page=page, page_size=page_size)
        return {
            "status": "ok",
            "data": {
                "servers": data["servers"],
                "total": data["total"],
                "total_pages": data["total_pages"],
                "page": data["page"],
                "page_size": data["page_size"],
                "pending_translate": data.get("pending_translate", False),
            },
        }

    @router.get("/api/mcp/installed")
    async def api_mcp_installed(user=Depends(get_current_user)):
        """已安装的 MCP server 列表"""
        return {"status": "ok", "data": list_installed()}

    @router.post("/api/mcp/install")
    async def api_mcp_install(req: McpInstallReq, user=Depends(require_admin)):
        """安装 MCP server（需管理员）

        自动判断 remote（http）还是 local（stdio）：
        - remote：从 Smithery API 拿 deploymentUrl + configSchema，用 https://mcp.<ns>.ai 直连
        - local：用用户提供的 command（npx/node/python...）
        """
        display = req.qualifiedName
        desc = ""
        namespace = req.qualifiedName.split("/")[0]

        # 拉取完整详情（含 remote 标志 + configSchema）
        full = await get_server_full(req.qualifiedName)
        if full:
            display = full.get("displayName") or display
            desc = full.get("description") or ""

        transport = req.transport
        url = req.url
        headers = dict(req.headers)
        command = req.command

        # auto 判断：remote server 走 http，否则 stdio
        if transport == "auto":
            is_remote = bool(full and full.get("remote"))
            if is_remote:
                transport = "http"
                # 优先用公开直连 URL（https://mcp.<ns>.ai），否则 deploymentUrl
                if not url:
                    url = resolve_mcp_url(req.qualifiedName, namespace)
            else:
                transport = "stdio"

        # 校验：http 需要 url，stdio 需要 command
        if transport == "http":
            if not url:
                raise HTTPException(400, "远程 server 缺少连接 URL")
            command = ""
        elif transport == "stdio":
            if not command or not command.strip():
                raise HTTPException(400, "本地 server 需填写启动命令（如 npx / node / python）")

        install_server(req.qualifiedName, display, desc, command or "", req.args, req.env,
                       transport=transport, url=url, headers=headers)

        # stdio 类型：修复 args 中可能被损坏的中文路径（Windows 编码问题）
        if transport == "stdio" and req.args:
            _fix_corrupted_args(req.qualifiedName)

        return {"status": "ok", "data": {"qualifiedName": req.qualifiedName, "installed": True, "transport": transport, "url": url}}

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
            raise BizError(ErrorCode.MCP_NOT_INSTALLED)
        try:
            if s.get("transport") == "http":
                tools = await list_mcp_tools_http(s["url"], headers=s.get("headers") or {})
            else:
                tools = await asyncio.to_thread(_list_mcp_tools_sync, s["command"], s["args"], s["env"])
            return {"status": "ok", "data": tools}
        except Exception as e:
            raise BizError(ErrorCode.MCP_CALL_FAILED, f"连接 MCP server 失败: {str(e)}")

    @router.post("/api/mcp/{qualified_name:path}/call")
    async def api_mcp_call(qualified_name: str, req: McpCallReq, user=Depends(require_admin)):
        """调用 MCP server 的工具（需管理员）"""
        s = get_installed(qualified_name)
        if not s:
            raise BizError(ErrorCode.MCP_NOT_INSTALLED)
        try:
            if s.get("transport") == "http":
                result = await call_mcp_tool_http(s["url"], req.tool, req.arguments,
                                                  headers=s.get("headers") or {})
            else:
                result = await asyncio.to_thread(_call_mcp_tool_sync, s["command"], req.tool, req.arguments, s["args"], s["env"])
            return {"status": "ok", "data": result}
        except Exception as e:
            raise BizError(ErrorCode.MCP_CALL_FAILED, f"MCP 工具调用失败: {str(e)}")
