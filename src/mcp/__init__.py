"""MCP 接入层 — 对外暴露统一入口"""
from .client import list_mcp_tools, call_mcp_tool
from .smithery import list_servers, get_server
from .manager import list_installed, install_server, uninstall_server, get_server as get_installed

__all__ = [
    "list_mcp_tools", "call_mcp_tool",
    "list_servers", "get_server",
    "list_installed", "install_server", "uninstall_server", "get_installed",
]
