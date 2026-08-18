"""
host.py — 插件子进程宿主（隔离 + 超时 + JSON-RPC 通信）

核心：插件跑在独立子进程，stdin/stdout 走 JSON-RPC，不入主进程。
稳定性三支柱之「隔离」在此落地。

通信协议（JSON-RPC 简化版，行分隔 JSON）：
  请求  → {"method": "<tool_name>", "params": {...}}
  响应  → {"result": {...}} 或 {"error": "...", "traceback": "..."}
"""
import json
import logging
import subprocess
import sys
import time
import threading
from pathlib import Path

logger = logging.getLogger("rag.plugins.host")

DEFAULT_TIMEOUT = 30  # 单次调用默认超时（秒）

# 插件进程启动脚本：读 stdin 行分隔 JSON，调入口函数，写 stdout
_BOOTSTRAP = r"""
import sys, json, importlib.util, traceback

def main():
    entry = sys.argv[1]
    spec = importlib.util.spec_from_file_location("plugin_main", entry)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            params = req.get("params", {})
            fn = getattr(mod, method, None)
            if fn is None:
                raise AttributeError(f"method not found: {method}")
            if callable(fn):
                result = fn(**params) if isinstance(params, dict) else fn(*params)
            else:
                result = fn
            print(json.dumps({"result": result}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
"""


class PluginHost:
    """管理单个插件的子进程实例"""

    def __init__(self, name: str, entry_path: str, timeout: int = DEFAULT_TIMEOUT):
        self.name = name
        self.entry_path = entry_path
        self.timeout = timeout
        self._proc = None

    def start(self) -> bool:
        """启动子进程，返回是否成功"""
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _BOOTSTRAP, self.entry_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            return True
        except Exception as e:
            logger.error(f"[Host] 启动插件 {self.name} 失败: {e}")
            return False

    def invoke(self, method: str, params: dict = None) -> dict:
        """调用插件方法，返回 {"result":...} 或 {"error":...}"""
        if self._proc is None or self._proc.poll() is not None:
            # 进程已死，尝试重启一次
            if not self.start():
                return {"error": f"插件 {self.name} 进程无法启动", "traceback": ""}
        try:
            req = json.dumps({"method": method, "params": params or {}}, ensure_ascii=False)
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()

            # 超时控制：用后台线程读 stdout（平台无关，Windows 上 select 不能用于 pipe）
            result = {}

            def _read_line():
                try:
                    line = self._proc.stdout.readline()
                    if not line:
                        result["err"] = {"error": f"插件 {self.name} 进程异常退出", "traceback": self._read_stderr_tail()}
                    else:
                        try:
                            result["ok"] = json.loads(line.strip())
                        except json.JSONDecodeError:
                            result["err"] = {"error": f"插件返回非法 JSON: {line.strip()[:200]}", "traceback": line.strip()}
                except Exception as e:
                    result["err"] = {"error": str(e), "traceback": ""}

            t = threading.Thread(target=_read_line, daemon=True)
            t.start()
            t.join(timeout=self.timeout)

            if t.is_alive():
                # 超时：读线程仍阻塞在 readline，直接 kill 进程，读者线程会随之结束
                self._kill()
                return {"error": f"插件 {self.name}.{method} 调用超时({self.timeout}s)，已终止", "traceback": ""}
            if "ok" in result:
                return result["ok"]
            return result.get("err", {"error": "插件无响应", "traceback": ""})
        except Exception as e:
            return {"error": str(e), "traceback": ""}

    def _read_stderr_tail(self) -> str:
        try:
            if self._proc and self._proc.stderr:
                return self._proc.stderr.read()[-2000:]
        except Exception:
            pass
        return ""

    def _kill(self):
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
                self._proc.wait(timeout=3)
        except Exception:
            pass

    def stop(self):
        self._kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class HostPool:
    """插件宿主池：name → PluginHost，惰性启动"""

    def __init__(self):
        self._hosts: dict[str, PluginHost] = {}

    def get_host(self, name: str, entry_path: str, timeout=None) -> PluginHost:
        if timeout is None:
            timeout = DEFAULT_TIMEOUT
        if name not in self._hosts:
            host = PluginHost(name, entry_path, timeout)
            host.start()
            self._hosts[name] = host
        return self._hosts[name]

    def invoke(self, name: str, entry_path: str, method: str, params: dict = None,
                timeout: float = None) -> dict:
        host = self.get_host(name, entry_path, timeout)
        return host.invoke(method, params)

    def stop_all(self):
        for host in self._hosts.values():
            host.stop()
        self._hosts.clear()


# 全局单例
_pool: HostPool | None = None


def get_pool() -> HostPool:
    global _pool
    if _pool is None:
        _pool = HostPool()
    return _pool
