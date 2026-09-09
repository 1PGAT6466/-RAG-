"""
MCP 客户端 — 连接外部 MCP server（stdio + HTTP 双传输）

复用项目插件系统的「子进程隔离」思想：
  - stdio：MCP server 作为独立子进程运行（stdio 通信）
  - http：连接远程 Streamable HTTP MCP server（如 https://mcp.exa.ai）
  - 调用带超时，超时/崩溃不影响主进程

用法：
  # stdio
  result = await call_mcp_tool(command, tool_name, arguments)
  # http
  result = await call_mcp_tool_http(url, tool_name, arguments, headers=...)
"""
import logging
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("rag.mcp.client")

CALL_TIMEOUT = 30
CONNECT_TIMEOUT = 60


async def list_mcp_tools(command: str, args: list[str] = None, env: dict = None) -> list[dict]:
    """连接 MCP server 并列出工具，返回 [{name, description, inputSchema}]"""
    params = StdioServerParameters(command=command, args=args or [], env=env or {})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
            resp = await session.list_tools()
    tools = []
    for t in resp.tools:
        tools.append({
            "name": t.name,
            "description": t.description or "",
            "inputSchema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
        })
    return tools


async def call_mcp_tool(command: str, tool_name: str, arguments: dict,
                        args: list[str] = None, env: dict = None) -> dict:
    """连接 MCP server → 调用工具 → 断开，返回 {content, isError} 或 {error}"""
    params = StdioServerParameters(command=command, args=args or [], env=env or {})
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
                resp = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments or {}),
                    timeout=CALL_TIMEOUT,
                )
    except asyncio.TimeoutError:
        logger.error(f"MCP 工具调用超时: {tool_name}")
        return {"error": f"工具调用超时（>{CALL_TIMEOUT}s）"}
    except Exception as e:
        logger.error(f"MCP 调用失败: {e}")
        return {"error": f"MCP 调用失败: {str(e)}"}

    # 提取文本内容
    contents = []
    is_error = getattr(resp, "isError", False)
    for c in resp.content:
        if hasattr(c, "text"):
            contents.append(c.text)
        else:
            contents.append(str(c))
    return {"content": "\n".join(contents), "isError": is_error}


# ============================================================
# HTTP 传输（Streamable HTTP MCP server，如 https://mcp.exa.ai）
# ============================================================
def _build_http_client(headers: dict | None = None):
    """构建带自定义 header 的 HTTP client（可传入 Authorization / API key 等）"""
    try:
        from mcp.client.streamable_http import create_mcp_http_client
        return create_mcp_http_client(headers=headers or None)
    except Exception:
        return None


async def list_mcp_tools_http(url: str, headers: dict | None = None) -> list[dict]:
    """连接远程 Streamable HTTP MCP server 并列出工具。"""
    from mcp.client.streamable_http import streamable_http_client
    http_client = _build_http_client(headers)
    kwargs = {"http_client": http_client} if http_client else {}
    async with streamable_http_client(url, **kwargs) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
            resp = await session.list_tools()
    tools = []
    for t in resp.tools:
        tools.append({
            "name": t.name,
            "description": t.description or "",
            "inputSchema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
        })
    return tools


async def call_mcp_tool_http(url: str, tool_name: str, arguments: dict,
                             headers: dict | None = None) -> dict:
    """连接远程 Streamable HTTP MCP server → 调用工具 → 断开。"""
    from mcp.client.streamable_http import streamable_http_client
    http_client = _build_http_client(headers)
    kwargs = {"http_client": http_client} if http_client else {}
    try:
        async with streamable_http_client(url, **kwargs) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
                resp = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments or {}),
                    timeout=CALL_TIMEOUT,
                )
    except asyncio.TimeoutError:
        logger.error(f"MCP(HTTP) 工具调用超时: {tool_name}")
        return {"error": f"工具调用超时（>{CALL_TIMEOUT}s）"}
    except Exception as e:
        logger.error(f"MCP(HTTP) 调用失败: {e}")
        return {"error": f"MCP(HTTP) 调用失败: {str(e)}"}

    contents = []
    is_error = getattr(resp, "isError", False)
    for c in resp.content:
        if hasattr(c, "text"):
            contents.append(c.text)
        else:
            contents.append(str(c))
    return {"content": "\n".join(contents), "isError": is_error}
