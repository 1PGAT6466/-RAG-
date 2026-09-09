"""MCP 接入层 — 对外暴露统一入口"""
from .client import list_mcp_tools, call_mcp_tool, list_mcp_tools_http, call_mcp_tool_http
from .smithery import list_servers, get_server, get_server_full, resolve_mcp_url
from .manager import list_installed, install_server, uninstall_server, get_server as get_installed

__all__ = [
    "list_mcp_tools", "call_mcp_tool", "list_mcp_tools_http", "call_mcp_tool_http",
    "list_servers", "get_server", "get_server_full", "resolve_mcp_url",
    "list_installed", "install_server", "uninstall_server", "get_installed",
]
