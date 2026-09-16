"""对话测试：用 auto/knowledge 模式"""
import requests

BASE = "http://127.0.0.1:8099"
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["token"]
H = {"Authorization": f"Bearer {token}"}

tests = [
    ("连接器镀金层厚度要求是什么", "knowledge"),
    ("采购数据中有哪些供应商", "knowledge"),
    ("非标准机械设计手册中热处理工艺有哪些", "knowledge"),
]

for q, mode in tests:
    r = requests.post(f"{BASE}/api/chat/stream", json={"query": q, "mode": mode}, headers=H, stream=True)
    answer = ""
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            answer += chunk
    
    # Get sources from a non-stream call
    r2 = requests.post(f"{BASE}/api/chat", json={"query": q, "mode": mode}, headers=H)
    d2 = r2.json()["data"]
    sources = d2.get("sources", [])
    src_files = set(s.get("file_name", "?") for s in sources)
    
    print(f"\n{'='*60}")
    print(f"📝 [{mode}] {q}")
    print(f"   引用: {len(sources)} 条 | 文件: {', '.join(src_files) if src_files else '无'}")
    print(f"   回复前200字: {answer[:200]}")
