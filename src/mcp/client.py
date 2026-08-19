"""
MCP 客户端 — 连接外部 MCP server（stdio 传输）

复用项目插件系统的「子进程隔离」思想：
  - MCP server 作为独立子进程运行（stdio 通信）
  - 调用带超时，超时/崩溃不影响主进程
  - 只连接白名单来源，避免任意代码执行风险

用法（mcp 2.0 正确姿势：async with 嵌套）：
  result = await call_mcp_tool(command, tool_name, arguments)
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
