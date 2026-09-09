"""端到端 RAG 全链路验证"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests, json, datetime
import jwt

BASE = "http://127.0.0.1:8099"
from config import JWT_SECRET
token = jwt.encode(
    {"sub": "admin", "user_id": 2, "role": "admin",
     "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
     "iat": datetime.datetime.now(datetime.timezone.utc)},
    JWT_SECRET, algorithm="HS256"
)
H = {"Authorization": f"Bearer {token}"}

print("=" * 60)
print("RAG 全链路端到端验证")
print("=" * 60)

# 1. 检索
print("\n[1] 检索测试")
r = requests.post(f"{BASE}/api/search", headers=H,
                  json={"query": "连接器接触电阻", "top_k": 5}, timeout=30)
data = r.json()["data"]
print(f"  检索到 {len(data)} 条结果")
for i, d in enumerate(data[:3]):
    print(f"  [{i+1}] score={d.get('score', 0):.4f} file={d.get('file_name', '')} source={d.get('source', '')}")
    print(f"      {d.get('content', '')[:80]}...")

# 2. RAG 对话 (knowledge 模式)
print("\n[2] RAG 对话测试 (knowledge 模式)")
r = requests.post(f"{BASE}/api/chat", headers=H,
                  json={"query": "连接器的接触电阻标准是多少", "top_k": 5, "mode": "knowledge"},
                  timeout=120)
d = r.json()["data"]
print(f"  模式: {d.get('mode')}")
print(f"  引用数: {len(d.get('sources', []))}")
answer = d.get("answer", "")
print(f"  回答前200字: {answer[:200]}")
has_citation = "[1]" in answer or "[2]" in answer
print(f"  含引用标注: {has_citation}")

# 3. 闲聊模式
print("\n[3] 闲聊模式测试")
r = requests.post(f"{BASE}/api/chat", headers=H,
                  json={"query": "你好", "top_k": 5, "mode": "chat"},
                  timeout=60)
d = r.json()["data"]
print(f"  模式: {d.get('mode')}")
print(f"  回答: {d.get('answer', '')[:100]}")

# 4. 实体图谱
print("\n[4] 实体图谱测试")
r = requests.get(f"{BASE}/api/entities/graph", headers=H, timeout=60)
g = r.json()["data"]
print(f"  节点: {len(g.get('nodes', []))}  边: {len(g.get('edges', []))}")

# 5. API 健壮性 (修复验证)
print("\n[5] API 健壮性验证")
r = requests.delete(f"{BASE}/api/documents/99999", headers=H, timeout=10)
print(f"  删除不存在文件: HTTP {r.status_code} (期望 404) {'PASS' if r.status_code == 404 else 'FAIL'}")

r = requests.put(f"{BASE}/api/documents/99999/category", headers=H,
                 json={"category": "测试"}, timeout=10)
print(f"  更新不存在文件分类: HTTP {r.status_code} (期望 404) {'PASS' if r.status_code == 404 else 'FAIL'}")

r = requests.put(f"{BASE}/api/documents/99999/tags", headers=H,
                 json={"tags": ["test"]}, timeout=10)
print(f"  更新不存在文件标签: HTTP {r.status_code} (期望 404) {'PASS' if r.status_code == 404 else 'FAIL'}")

# 6. ChatReq 校验 (修复验证)
print("\n[6] 输入校验验证")
r = requests.post(f"{BASE}/api/chat", headers=H,
                  json={"query": "test", "mode": "invalid_mode"}, timeout=10)
print(f"  非法 mode: HTTP {r.status_code} (期望 422) {'PASS' if r.status_code == 422 else 'FAIL'}")

# 7. 检索语义查询 (H2 修复: 向量结果不再无条件丢弃)
print("\n[7] 语义检索测试 (H2 修复验证)")
r = requests.post(f"{BASE}/api/search", headers=H,
                  json={"query": "怎么防锈", "top_k": 5}, timeout=30)
data = r.json()["data"]
print(f"  语义查询检索到 {len(data)} 条结果")

# 8. 乱码过滤 (应返回空)
print("\n[8] 乱码过滤测试")
r = requests.post(f"{BASE}/api/search", headers=H,
                  json={"query": "zzzzqqqqxxxx", "top_k": 5}, timeout=30)
data = r.json()["data"]
print(f"  乱码查询: {len(data)} 条 (期望 0) {'PASS' if len(data) == 0 else 'FAIL'}")

print("\n" + "=" * 60)
print("全链路验证完成")
print("=" * 60)
