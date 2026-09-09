"""
检索质量回归基准 —— 可重复运行的权威基线

用法：
  python scripts/retrieval_benchmark.py [--base http://127.0.0.1:8099]

每条用例的「期望文档」都已用 SQL 核对过知识库真实内容（2026-08-25 校准），
避免拍脑袋的错误期望。工业精确类 + OA 语义类共 20 条。

校验逻辑：top1 结果的文件名包含「期望关键词」即判 PASS。
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import json
import datetime
import jwt
from config import JWT_SECRET

# (query, 期望文件名包含的关键词)
# 已校准：FAKRA 期望改 Mini-fakra（知识库无独立 FAKRA 文档）；系统参数/轴承等按真实内容
CASES = [
    # 工业精确
    ("连接器接触电阻标准是多少", "Foxconn_连接器设计手册"),
    ("镀金层厚度要求", "Foxconn_连接器设计手册"),
    ("连接器镀金层接触电阻", "Foxconn_连接器设计手册"),
    ("FAKRA连接器规格", "Mini-fakra"),  # 校准：知识库仅 Mini-fakra 文档含 FAKRA
    ("Mini-fakra 耐压测试电压", "Mini-fakra"),
    ("Mini-fakra 绝缘电阻要求", "Mini-fakra"),
    ("Mini-fakra 漏电流标准", "Mini-fakra"),
    ("LCP材料特性", "Foxconn_连接器设计手册"),
    ("PA66和PBT材料区别", "Foxconn_连接器设计手册"),
    ("GB/T 3077 20Mn2 抗拉强度", "非标准机械设计手册"),
    ("20Mn2 屈服强度", "非标准机械设计手册"),
    ("线性导轨选型", "标准件新表"),
    ("轴承型号", "标准件新表"),
    # OA 语义
    ("如何新建公文流程", "公文"),
    ("证照如何管理", "证照"),
    ("系统参数在哪里设置", "系统参数"),
    ("门户如何配置", "门户"),
    ("人事模块怎么用", "人事"),
    ("报表功能说明", "报表"),
    ("SAP集成配置", "SAP"),
]


def make_token():
    return jwt.encode(
        {"sub": "admin", "user_id": 2, "role": "admin",
         "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
         "iat": datetime.datetime.now(datetime.timezone.utc)},
        JWT_SECRET, algorithm="HS256",
    )


def run(base: str, save: str = None):
    H = {"Authorization": f"Bearer {make_token()}"}
    results = []
    for q, expect in CASES:
        try:
            r = requests.post(f"{base}/api/search",
                              json={"query": q, "top_k": 5},
                              headers=H, timeout=30)
            hits = r.json().get("data", [])
            top = hits[0] if hits else {}
            top_file = top.get("file_name", "")
            hit = expect in top_file
            results.append({
                "query": q, "expect": expect, "top_file": top_file,
                "score": top.get("score", 0), "pass": hit,
            })
            print(f"[{'PASS' if hit else 'FAIL'}] {q[:22]:22s} -> {top_file[:36]}")
        except Exception as e:
            results.append({"query": q, "error": str(e), "pass": False})
            print(f"[ERR]  {q[:22]:22s} -> {e}")

    passed = sum(1 for r in results if r.get("pass"))
    total = len(CASES)
    print(f"\n=== 检索基线：{passed}/{total} ===")
    if save:
        with open(save, "w", encoding="utf-8") as f:
            json.dump({"total": total, "passed": passed, "results": results},
                      f, ensure_ascii=False, indent=2)
        print(f"结果已保存 -> {save}")
    return passed, total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--save", default=None)
    args = ap.parse_args()
    run(args.base, args.save)
