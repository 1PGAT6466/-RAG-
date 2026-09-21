"""用 Python requests 测试搜索全链路（绕过 PowerShell 编码问题）"""
import requests, json

BASE = "http://127.0.0.1:8099"

# 1. 登录
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["token"]
H = {"Authorization": f"Bearer {token}"}
print(f"✅ 登录成功")

# 2. 搜索测试
queries = ["连接器", "供应商", "镀金层厚度要求", "采购订单", "伺服压装"]
print(f"\n{'='*60}")
print(f"搜索测试（共 {len(queries)} 条）")
print(f"{'='*60}")

for q in queries:
    r = requests.post(f"{BASE}/api/search/debug", json={"query": q, "top_k": 5}, headers=H)
    d = r.json()["data"]
    bm25 = len(d["recalls"]["bm25"])
    vec = len(d["recalls"]["vector"])
    fusion = len(d["fusion"])
    final = len(d["final"])
    kind = d["kind"]
    
    final_files = set()
    for f in d["final"]:
        final_files.add(f.get("file_name", "?"))
    
    status = "✅" if final > 0 else "❌"
    print(f"\n{status} 「{q}」")
    print(f"   BM25={bm25} | Vector={vec} | Fusion={fusion} | Final={final} | kind={kind}")
    if final_files:
        print(f"   命中文件: {', '.join(final_files)}")

# 3. 对话测试
print(f"\n{'='*60}")
print(f"对话测试")
print(f"{'='*60}")

chat_queries = [
    ("连接器镀金层厚度要求是什么", "chat"),
    ("采购数据中有哪些供应商", "chat"),
]

for q, mode in chat_queries:
    r = requests.post(f"{BASE}/api/chat", json={"query": q, "mode": mode}, headers=H)
    d = r.json()["data"]
    answer = d.get("answer", "")
    sources = d.get("sources", [])
    src_files = set(s.get("file_name", "?") for s in sources)
    
    print(f"\n📝 「{q}」")
    print(f"   引用: {len(sources)} 条 | 文件: {', '.join(src_files) if src_files else '无'}")
    print(f"   回复: {answer[:150]}...")

print(f"\n{'='*60}")
print(f"测试完成")
print(f"{'='*60}")
