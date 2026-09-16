"""
检索质量回归基准（P15 增强版）
==============================

用法：
  python scripts/retrieval_benchmark.py                        # 在线模式（需运行服务）
  python scripts/retrieval_benchmark.py --offline              # 离线模式（直接调 search 模块）
  python scripts/retrieval_benchmark.py --save result.json     # 保存结果
  python scripts/retrieval_benchmark.py --k 5                  # 用 Recall@5

指标：
  - Recall@K：top-K 中是否包含期望文档（每条用例单独判定）
  - MRR (Mean Reciprocal Rank)：第一个相关结果的倒数排名均值
  - nDCG@K：归一化折扣累积增益（二元相关性）
  - 分类统计：工业精确类 / OA 语义类分别报告

Golden Set 结构：
  每条用例 = (query, [期望文件名列表], category)
  category: "industrial"（工业精确） | "semantic"（OA 语义）
  多个期望文件名：任一命中即算相关（OR 语义）

校准说明（2026-08-25 基线，2026-09-10 扩充）：
  - 工业精确类：基于知识库真实文档内容校准
  - OA 语义类：当前知识库无 OA 文档，预期全 FAIL（基线记录用，不作为回归判定）
"""
import sys
import argparse
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import datetime
import jwt
from config import JWT_SECRET

# (query, [期望文件名关键词列表], category)
# 多个关键词 = OR 语义：任一命中即相关
GOLDEN_SET = [
    # === 工业精确类（industrial）===
    # 连接器
    ("连接器接触电阻标准是多少", ["Foxconn_连接器设计手册"], "industrial"),
    ("镀金层厚度要求", ["Foxconn_连接器设计手册"], "industrial"),
    ("连接器镀金层接触电阻", ["Foxconn_连接器设计手册"], "industrial"),
    ("FAKRA连接器规格", ["Mini-fakra", "Foxconn_连接器设计手册"], "industrial"),
    ("Mini-fakra 耐压测试电压", ["Mini-fakra"], "industrial"),
    ("Mini-fakra 绝缘电阻要求", ["Mini-fakra"], "industrial"),
    ("Mini-fakra 漏电流标准", ["Mini-fakra"], "industrial"),
    ("HSD连接器阻抗", ["Foxconn_连接器设计手册"], "industrial"),
    ("MLG系列连接器型号", ["Foxconn_连接器设计手册"], "industrial"),
    # 材料
    ("LCP材料特性", ["Foxconn_连接器设计手册"], "industrial"),
    ("PA66和PBT材料区别", ["Foxconn_连接器设计手册"], "industrial"),
    ("磷青铜导体特性", ["Foxconn_连接器设计手册"], "industrial"),
    # 标准件
    ("线性导轨选型", ["标准件新表"], "industrial"),
    ("轴承型号", ["标准件新表"], "industrial"),
    ("标准件导轨规格", ["标准件新表"], "industrial"),
    # 标准号
    ("GB/T 3077 20Mn2 抗拉强度", ["非标准机械设计手册"], "industrial"),
    ("20Mn2 屈服强度", ["非标准机械设计手册"], "industrial"),
    ("QC/T 标准连接器", ["Foxconn_连接器设计手册"], "industrial"),
    # 工艺
    ("焊接工艺要求", ["Foxconn_连接器设计手册"], "industrial"),
    ("电镀工艺规范", ["Foxconn_连接器设计手册"], "industrial"),
    ("注塑成型工艺", ["Foxconn_连接器设计手册"], "industrial"),
    # 参数
    ("阻抗匹配要求", ["Foxconn_连接器设计手册"], "industrial"),
    ("频率范围指标", ["Foxconn_连接器设计手册"], "industrial"),
    ("额定电压电流", ["Foxconn_连接器设计手册"], "industrial"),
    ("温度范围要求", ["Foxconn_连接器设计手册"], "industrial"),

    # === OA 语义类（semantic）===
    # 注：当前知识库无 OA 文档，这些用例预期 FAIL（基线记录，不作为回归判定）
    ("如何新建公文流程", ["公文"], "semantic"),
    ("证照如何管理", ["证照"], "semantic"),
    ("系统参数在哪里设置", ["系统参数"], "semantic"),
    ("门户如何配置", ["门户"], "semantic"),
    ("人事模块怎么用", ["人事"], "semantic"),
    ("报表功能说明", ["报表"], "semantic"),
    ("SAP集成配置", ["SAP"], "semantic"),
]


def make_token():
    return jwt.encode(
        {"sub": "admin", "user_id": 2, "role": "admin",
         "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
         "iat": datetime.datetime.now(datetime.timezone.utc)},
        JWT_SECRET, algorithm="HS256",
    )


def dcg_at_k(relevances: list[int], k: int) -> float:
    """计算 DCG@K（二元相关性）"""
    dcg = 0.0
    for i in range(min(k, len(relevances))):
        dcg += relevances[i] / math.log2(i + 2)  # i+2 because log2(1) = 0
    return dcg


def ndcg_at_k(relevances: list[int], k: int) -> float:
    """计算 nDCG@K"""
    dcg = dcg_at_k(relevances, k)
    ideal = sorted(relevances, reverse=True)
    idcg = dcg_at_k(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg / idcg


def is_relevant(file_name: str, expected_keywords: list[str]) -> bool:
    """判断结果是否相关（任一关键词命中即相关）"""
    if not file_name:
        return False
    return any(kw in file_name for kw in expected_keywords)


def evaluate_case_online(base: str, headers: dict, query: str, expected: list[str], k: int) -> dict:
    """在线模式：调 API 评估单条用例"""
    import requests
    try:
        r = requests.post(f"{base}/api/search",
                          json={"query": query, "top_k": k},
                          headers=headers, timeout=30)
        hits = r.json().get("data", [])
        return _score_hits(hits, expected, k)
    except Exception as e:
        return {"error": str(e), "recall": 0, "rr": 0, "ndcg": 0.0, "hits": []}


def evaluate_case_offline(query: str, expected: list[str], k: int) -> dict:
    """离线模式：直接调 search 模块评估单条用例（search 是 async，用 asyncio.run 包装）"""
    try:
        import asyncio
        from src.retrieval.search import search
        hits = asyncio.run(search(query, top_k=k))
        return _score_hits(hits, expected, k)
    except Exception as e:
        return {"error": str(e), "recall": 0, "rr": 0, "ndcg": 0.0, "hits": []}


def _score_hits(hits: list, expected: list[str], k: int) -> dict:
    """对检索结果打分（同时计算 nDCG@5 和 nDCG@10）"""
    relevances = []
    first_relevant_rank = None
    hit_files = []

    for i, h in enumerate(hits):
        fname = h.get("file_name", "")
        rel = 1 if is_relevant(fname, expected) else 0
        relevances.append(rel)
        if rel and first_relevant_rank is None:
            first_relevant_rank = i + 1  # 1-indexed
            hit_files.append(fname)

    recall = 1 if first_relevant_rank is not None else 0
    rr = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
    ndcg = ndcg_at_k(relevances, k)
    ndcg_5 = ndcg_at_k(relevances, 5)
    ndcg_10 = ndcg_at_k(relevances, 10)

    return {
        "recall": recall,
        "rr": rr,
        "ndcg": ndcg,
        "ndcg@5": ndcg_5,
        "ndcg@10": ndcg_10,
        "first_rank": first_relevant_rank,
        "hit_files": hit_files,
        "top_file": hits[0].get("file_name", "") if hits else "",
    }


def run(base: str = None, k: int = 5, save: str = None, offline: bool = False):
    """运行评测"""
    headers = {}
    if not offline:
        import requests  # noqa: F401
        headers = {"Authorization": f"Bearer {make_token()}"}

    results = []
    for query, expected, category in GOLDEN_SET:
        if offline:
            r = evaluate_case_offline(query, expected, k)
        else:
            r = evaluate_case_online(base, headers, query, expected, k)

        r["query"] = query
        r["expected"] = expected
        r["category"] = category
        results.append(r)

        status = "PASS" if r["recall"] else "FAIL"
        top = r.get("top_file", "")[:36]
        rr_str = f"RR={r['rr']:.2f}" if r["rr"] else ""
        print(f"[{status}] {query[:22]:22s} -> {top} {rr_str}")

    # 汇总
    ind = [r for r in results if r["category"] == "industrial"]
    sem = [r for r in results if r["category"] == "semantic"]

    def _summary(cases, label):
        n = len(cases)
        if n == 0:
            return
        recall_n = sum(1 for r in cases if r["recall"])
        mrr = sum(r["rr"] for r in cases) / n
        avg_ndcg = sum(r["ndcg"] for r in cases) / n
        avg_ndcg_5 = sum(r.get("ndcg@5", 0) for r in cases) / n
        avg_ndcg_10 = sum(r.get("ndcg@10", 0) for r in cases) / n
        print(f"\n  {label} ({n} 条):")
        print(f"    Recall@{k}: {recall_n}/{n} = {recall_n/n:.1%}")
        print(f"    MRR:       {mrr:.3f}")
        print(f"    nDCG@5:    {avg_ndcg_5:.3f}")
        print(f"    nDCG@10:   {avg_ndcg_10:.3f}")

    print(f"\n{'='*60}")
    print(f"检索基线报告 (k={k})")
    print(f"{'='*60}")
    _summary(ind, "工业精确类")
    _summary(sem, "OA 语义类（当前无 OA 文档，预期低分）")

    total = len(results)
    total_recall = sum(1 for r in results if r["recall"])
    total_mrr = sum(r["rr"] for r in results) / total
    total_ndcg_5 = sum(r.get("ndcg@5", 0) for r in results) / total
    total_ndcg_10 = sum(r.get("ndcg@10", 0) for r in results) / total
    print(f"\n  总体 ({total} 条):")
    print(f"    Recall@{k}: {total_recall}/{total} = {total_recall/total:.1%}")
    print(f"    MRR:       {total_mrr:.3f}")
    print(f"    nDCG@5:    {total_ndcg_5:.3f}")
    print(f"    nDCG@10:   {total_ndcg_10:.3f}")

    # 仅工业类判定（OA 类无文档数据，不计入回归判定）
    ind_recall = sum(1 for r in ind if r["recall"])
    ind_total = len(ind)
    print(f"\n  [判定] 工业精确类 Recall@{k}: {ind_recall}/{ind_total}")
    if ind_recall >= ind_total * 0.8:
        print(f"  ✅ 通过（≥80%）")
    else:
        print(f"  ❌ 未通过（<80%）")

    if save:
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "k": k,
            "total": total,
            "industrial": {"total": len(ind), "recall": sum(1 for r in ind if r["recall"]),
                           "mrr": sum(r["rr"] for r in ind) / max(len(ind), 1)},
            "semantic": {"total": len(sem), "recall": sum(1 for r in sem if r["recall"]),
                         "mrr": sum(r["rr"] for r in sem) / max(len(sem), 1)},
            "results": results,
        }
        with open(save, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存 -> {save}")

    return total_recall, total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--k", type=int, default=5, help="Recall@K 的 K 值")
    ap.add_argument("--save", default=None, help="保存结果到 JSON 文件")
    ap.add_argument("--offline", action="store_true", help="离线模式（直接调 search 模块）")
    args = ap.parse_args()

    if args.offline:
        print("离线模式：直接调用 search 模块")
        run(k=args.k, save=args.save, offline=True)
    else:
        run(base=args.base, k=args.k, save=args.save, offline=False)
