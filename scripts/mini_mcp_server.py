"""
极简 MCP server（测试用）— 验证 client 连接 + 工具列表 + 调用
"""
import sys
import json


def main():
    # 简单的 stdio JSON-RPC 循环（只实现 initialize + tools/list + tools/call）
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        method = req.get("method")

        if method == "initialize":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid, "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mini-test", "version": "1.0.0"},
                }
            }))
        elif method == "notifications/initialized":
            pass  # 通知，无需响应
        elif method == "tools/list":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid, "result": {"tools": [
                    {"name": "echo", "description": "回显输入", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
                    {"name": "add", "description": "两数相加", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
                ]}
            }))
        elif method == "tools/call":
            name = req.get("params", {}).get("name")
            args = req.get("params", {}).get("arguments", {})
            if name == "echo":
                result = f"你说了: {args.get('text', '')}"
            elif name == "add":
                result = str(args.get("a", 0) + args.get("b", 0))
            else:
                result = "unknown tool"
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": result}],
                    "isError": False,
                }
            }))
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}}))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
