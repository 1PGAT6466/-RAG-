"""
综合冒烟测试 — 全链路健康检查

覆盖：认证、文档、检索、三模式对话、实体图谱、文档图谱、插件、清洗归一化、权限、参数校验
用法：python scripts/smoke_test.py [--base http://127.0.0.1:8099]
"""
import sys
import json
import time
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8099"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]

# 用 JWT_SECRET 直接生成 token（避免依赖密码）
from config import JWT_SECRET
import jwt as pyjwt
import datetime

def make_token(role="admin"):
    payload = {
        "sub": "admin" if role == "admin" else "user",
        "user_id": 2 if role == "admin" else 36,
        "role": role,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        "iat": datetime.datetime.utcnow(),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

def H(role="admin"):
    return {"Authorization": f"Bearer {make_token(role)}"}

PASS = 0
FAIL = 0
FAILED = []

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name} {extra}")

print("=" * 60)
print("伏羲 RAG 框架 — 综合冒烟测试")
print("=" * 60)

# ========== 1. 健康检查 ==========
print("\n[1] 健康检查")
r = requests.get(f"{BASE}/api/health", timeout=10)
check("health 200", r.status_code == 200, f"(got {r.status_code})")

# ========== 2. 认证 ==========
print("\n[2] 认证")
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
check("登录 admin/admin123", r.status_code == 200 and ("token" in r.json().get("data", {}) or "access_token" in r.json()), f"(got {r.status_code})")
r = requests.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "wrong"}, timeout=10)
check("错误密码 401", r.status_code == 401, f"(got {r.status_code})")

# ========== 3. 文档 ==========
print("\n[3] 文档管理")
r = requests.get(f"{BASE}/api/documents", headers=H(), timeout=30)
d = r.json()
files = d.get("data", [])
check("文档列表", r.status_code == 200 and len(files) >= 1, f"(got {len(files)} files)")
r = requests.get(f"{BASE}/api/documents/99999", headers=H(), timeout=30)
check("不存在文档 404", r.status_code == 404, f"(got {r.status_code})")

# ========== 4. 检索 ==========
print("\n[4] 检索")
r = requests.post(f"{BASE}/api/search", headers=H(), json={"query": "连接器接触电阻", "top_k": 5}, timeout=30)
check("中文检索命中", r.status_code == 200 and len(r.json()["data"]) > 0, f"(got {len(r.json()['data']) if r.status_code==200 else 'err'})")
r = requests.post(f"{BASE}/api/search", headers=H(), json={"query": "zzzzqqqqxxxx", "top_k": 5}, timeout=30)
check("乱码检索 0 条", r.status_code == 200 and len(r.json()["data"]) == 0, f"(got {len(r.json()['data']) if r.status_code==200 else 'err'})")

# ========== 5. 三模式对话 ==========
print("\n[5] 三模式对话")
def chat(q, mode="auto"):
    r = requests.post(f"{BASE}/api/chat", headers=H(), json={"query": q, "top_k": 5, "mode": mode}, timeout=120)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}", 0
    d = r.json()["data"]
    return d.get("mode"), d.get("answer", ""), len(d.get("sources", []))

m, a, s = chat("你好，介绍一下你自己")
check("闲聊(auto→chat)", m == "chat" and len(a) > 10, f"(mode={m})")

m, a, s = chat("连接器镀金层厚度标准")
check("RAG(auto→knowledge)", m == "knowledge", f"(mode={m})")

m, a, s = chat("今天北京天气怎么样")
check("联网(auto→web)", m == "web" and s > 0, f"(mode={m}, sources={s})")

m, a, s = chat("给我讲个笑话", mode="chat")
check("强制 chat 模式", m == "chat", f"(mode={m})")

# ========== 6. 图谱 ==========
print("\n[6] 知识图谱")
r = requests.get(f"{BASE}/api/entities/graph", headers=H(), timeout=60)
g = r.json().get("data", {})
check("实体图谱有节点/边", len(g.get("nodes", [])) > 0 and len(g.get("edges", [])) > 0, f"({len(g.get('nodes',[]))}节点/{len(g.get('edges',[]))}边)")
r = requests.get(f"{BASE}/api/graph", headers=H(), timeout=30)
g2 = r.json().get("data", {})
check("文档图谱", len(g2.get("nodes", [])) > 0, f"({len(g2.get('nodes',[]))}节点)")

# 实体详情（反链）
if g.get("nodes"):
    eid = g["nodes"][0]["id"]
    r = requests.get(f"{BASE}/api/entities/{eid}", headers=H(), timeout=30)
    check("实体详情(反链)", r.status_code == 200 and "entity" in r.json().get("data", {}), f"(got {r.status_code})")

# ========== 7. 插件 ==========
print("\n[7] 插件")
r = requests.get(f"{BASE}/api/plugins", headers=H(), timeout=10)
plugins = r.json().get("data", [])
check("插件列表", r.status_code == 200 and len(plugins) >= 2, f"(got {len(plugins)} plugins)")

# ========== 8. 清洗归一化 ==========
print("\n[8] 清洗归一化（直接测模块）")
from src.pipeline.language_filter import normalize_han, filter_chunk, normalize_chunks
r1 = normalize_han("這個連接器的接觸電阻")
check("繁转简", r1 == "这个连接器的接触电阻", f"(got {r1})")
r2 = filter_chunk("连接器 コネクタ 设计")
check("去日文", "コネクタ" not in r2, f"(got {r2})")
r3 = normalize_han("乾燥 頭髮 後面")
check("歧义字不转", "乾" in r3 and "髮" in r3 and "後" in r3, f"(got {r3})")

# ========== 9. 权限 ==========
print("\n[9] 权限/RBAC")
r = requests.get(f"{BASE}/api/documents", timeout=10)
check("未登录 401", r.status_code == 401, f"(got {r.status_code})")
r = requests.get(f"{BASE}/api/plugins", headers=H("user"), timeout=10)
check("普通用户可读插件", r.status_code == 200, f"(got {r.status_code})")

# ========== 10. 参数校验 ==========
print("\n[10] 参数校验")
r = requests.post(f"{BASE}/api/search", headers=H(), json={"query": "", "top_k": 5}, timeout=10)
check("空 query 422", r.status_code == 422, f"(got {r.status_code})")
r = requests.post(f"{BASE}/api/search", headers=H(), json={"query": "test", "top_k": 999}, timeout=10)
check("top_k 越界 422", r.status_code == 422, f"(got {r.status_code})")

# ========== 结果 ==========
print("\n" + "=" * 60)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
if FAILED:
    print(f"失败项：{FAILED}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
