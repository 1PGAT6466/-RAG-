"""
MCP 客户端 — 连接外部 MCP server（stdio + HTTP 双传输）

绕过 mcp SDK 的 anyio cancel scope 问题，用 subprocess 直接通信。
"""
import json
import logging
import subprocess
import sys
import threading
import time
import shutil

logger = logging.getLogger("rag.mcp.client")

CALL_TIMEOUT = 30       # 工具调用超时（秒）
CONNECT_TIMEOUT = 60    # 连接/初始化超时（秒）


class _McpProcess:
    """管理单个 MCP server 子进程的 JSON-RPC 通信"""

    def __init__(self, command: str, args: list[str], env: dict | None = None):
        self.command = command
        self.args = args
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = threading.Lock()

    def _start(self):
        if self._proc and self._proc.poll() is None:
            return

        cmd = self.command
        # Windows: .CMD 文件需要通过 cmd /c 调用
        if sys.platform == "win32" and cmd.upper().endswith(".CMD"):
            full_cmd = ["cmd", "/c", cmd] + self.args
        else:
            full_cmd = [cmd] + self.args

        # 合并环境变量
        import os
        proc_env = {**os.environ}
        if self.env:
            proc_env.update(self.env)
        proc_env.setdefault("PYTHONUTF8", "1")

        self._proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            encoding="utf-8",
            bufsize=1,
        )
        logger.info(f"MCP server 启动: PID={self._proc.pid} cmd={cmd}")

        # 等待 server 就绪（读 stderr 的首行）
        ready_line = self._proc.stderr.readline()
        if ready_line:
            logger.info(f"MCP server stderr: {ready_line.strip()}")

    def _send_request(self, method: str, params: dict = None, timeout: float = 30) -> dict:
        """发送 JSON-RPC 请求并等待响应"""
        with self._lock:
            self._start()
            self._req_id += 1
            req = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
            }
            if params:
                req["params"] = params

            req_str = json.dumps(req, ensure_ascii=False)
            self._proc.stdin.write(req_str + "\n")
            self._proc.stdin.flush()

            # 读响应（带超时）
            result = {}
            def _read():
                try:
                    line = self._proc.stdout.readline()
                    if line:
                        result["data"] = json.loads(line.strip())
                    else:
                        result["error"] = "进程无输出"
                except json.JSONDecodeError as e:
                    result["error"] = f"JSON 解析失败: {e}"
                except Exception as e:
                    result["error"] = str(e)

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if t.is_alive():
                logger.error(f"MCP 响应超时 ({timeout}s)")
                self._kill()
                return {"error": f"响应超时 ({timeout}s)"}

            if "error" in result:
                return {"error": result["error"]}
            return result.get("data", {"error": "无响应"})

    def _kill(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
        self._proc = None

    def close(self):
        self._kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


# 连接池：key -> _McpProcess
_pool: dict[str, _McpProcess] = {}
_pool_lock = threading.Lock()


def _pool_key(command: str, args: list[str], env: dict | None) -> str:
    raw = f"{command}|{' '.join(args)}|{sorted((env or {}).items())}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


import hashlib


def _get_or_create(command: str, args: list[str], env: dict | None) -> _McpProcess:
    key = _pool_key(command, args, env)
    with _pool_lock:
        proc = _pool.get(key)
        if proc and proc.alive:
            return proc
        if proc:
            proc.close()
        proc = _McpProcess(command, args, env)
        _pool[key] = proc
        return proc


def _initialize_server(proc: _McpProcess) -> dict:
    """发送 MCP initialize 请求"""
    return proc._send_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "fuxi-rag", "version": "1.0"}
    }, timeout=CONNECT_TIMEOUT)


def list_mcp_tools(command: str, args: list[str] = None, env: dict = None) -> list[dict]:
    """连接 MCP server 并列出工具"""
    proc = _get_or_create(command, args or [], env)
    _initialize_server(proc)

    resp = proc._send_request("tools/list", timeout=CALL_TIMEOUT)
    if "error" in resp:
        raise RuntimeError(f"MCP 工具列表失败: {resp['error']}")

    tools = []
    for t in resp.get("result", {}).get("tools", []):
        tools.append({
            "name": t.get("name"),
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema", {}),
        })
    return tools


def call_mcp_tool(command: str, tool_name: str, arguments: dict,
                  args: list[str] = None, env: dict = None) -> dict:
    """调用 MCP server 的工具"""
    proc = _get_or_create(command, args or [], env)
    _initialize_server(proc)

    resp = proc._send_request("tools/call", {
        "name": tool_name,
        "arguments": arguments or {},
    }, timeout=CALL_TIMEOUT)

    if "error" in resp:
        return {"error": resp["error"]}

    result = resp.get("result", {})
    contents = []
    for c in result.get("content", []):
        if c.get("type") == "text":
            contents.append(c.get("text", ""))
        else:
            contents.append(str(c))
    return {"content": "\n".join(contents), "isError": result.get("isError", False)}


# ============================================================
# HTTP 传输（Streamable HTTP MCP server）
# ============================================================
async def list_mcp_tools_http(url: str, headers: dict | None = None) -> list[dict]:
    """连接远程 Streamable HTTP MCP server 并列出工具。"""
    from mcp.client.streamable_http import streamablehttp_client as _http_client
    from mcp import ClientSession
    import asyncio
    async with _http_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT)
            resp = await session.list_tools()
    return [{
        "name": t.name,
        "description": t.description or "",
        "inputSchema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
    } for t in resp.tools]


async def call_mcp_tool_http(url: str, tool_name: str, arguments: dict,
                             headers: dict | None = None) -> dict:
    """连接远程 Streamable HTTP MCP server → 调用工具 → 断开。"""
    from mcp.client.streamable_http import streamablehttp_client as _http_client
    from mcp import ClientSession
    import asyncio
    try:
        async with _http_client(url, headers=headers) as (read, write, _):
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
