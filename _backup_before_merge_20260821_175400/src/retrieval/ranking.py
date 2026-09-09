"""
ranking.py — 动态融合权重 + 精确型号匹配（移植自 RAG伏羲 dynamic_alpha.py + fusion.py）

用途：根据 query 类型动态调整 BM25/向量权重，并对工业型号/编号做精确匹配加权。
针对连接器/机械设计领域做了型号正则适配。
"""
import re
import logging

from src.classification import CATEGORY_DICT

logger = logging.getLogger("rag.ranking")


def classify_query(query: str) -> str:
    """分类查询类型：factual / semantic / action / hybrid"""
    q = query.lower()

    factual_kw = ["多少", "什么型号", "参数", "规格", "尺寸", "温度", "压力", "材质",
                  "标准", "gb", "iso", "型号", "料号", "公差", "阻抗", "电压", "电流"]
    if any(kw in q for kw in factual_kw):
        return "factual"

    semantic_kw = ["区别", "对比", "优缺点", "原理", "为什么", "怎么选", "推荐", "适合",
                   "是什么", "含义", "定义", "作用", "用途"]
    if any(kw in q for kw in semantic_kw):
        return "semantic"

    action_kw = ["怎么", "如何", "步骤", "方法", "操作", "设置", "配置", "安装", "使用",
                 "流程", "工艺", "装配", "检测"]
    if any(kw in q for kw in action_kw):
        return "action"

    return "hybrid"


def get_dynamic_alpha(query: str):
    """返回 (vector_weight, bm25_weight)"""
    qtype = classify_query(query)
    alpha_map = {
        "factual":  (0.35, 0.65),  # BM25 为主（精确匹配）
        "semantic": (0.75, 0.25),  # 向量为主（语义理解）
        "action":   (0.55, 0.45),
        "hybrid":   (0.60, 0.40),
    }
    return alpha_map.get(qtype, (0.60, 0.40))


# 连接器/机械领域的型号、编号、料号精确匹配模式
_MODEL_PATTERNS = [
    r"mini[\s-]?fakra",                          # Mini-FAKRA / mini fakra
    r"fakra",
    r"\b[a-z]{2,5}[\s-]?\d{2,6}([\s-]?\w+)?\b",  # GP-20-150, EP-123, AR1, LSW1
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",   # IP 地址（若涉及网络文档）
    r"\b\d+\.\d+\s?mm\b",                        # 尺寸 3.5mm
    r"\bpom\b", r"\bpa66\b", r"\bpa6\b", r"\babs\b", r"\bpp\b", r"\blcp\b",  # 材料型号
]

# 分类关键词表（工业题材，统一走 src.classification.CATEGORY_DICT 权威词典）
_CATEGORY_KW = CATEGORY_DICT


def detect_exact_models(query: str) -> list:
    """检测 query 中出现的精确型号/编号/材料模式"""
    found = []
    for pat in _MODEL_PATTERNS:
        for m in re.finditer(pat, query, re.IGNORECASE):
            token = m.group(0).strip()
            if token and token.lower() not in found:
                found.append(token.lower())
    return found


def _query_terms(q_lower: str) -> list[str]:
    """把 query 拆成关键词。中文用 jieba，英文/数字用空格拆分。
    原实现用 str.split()，对无空格的中文查询返回整句导致关键词命中恒为 0。
    """
    terms = []
    # 英文/数字词（空格分隔）
    for t in q_lower.split():
        if len(t) >= 2:
            terms.append(t)
    # 中文词（jieba），不足则整句兜底
    try:
        import jieba
        for w in jieba.lcut(q_lower):
            w = w.strip()
            if len(w) >= 2 and w not in terms:
                terms.append(w)
    except ImportError:
        pass
    if not terms and len(q_lower) >= 2:
        terms.append(q_lower)
    return terms


def exact_match_boost(query: str, results: list) -> list:
    """精确匹配 + 型号/编号加权"""
    if not results:
        return results
    q_lower = query.lower()
    exact_models = detect_exact_models(query)
    # 关键词只切一次（原实现每个 chunk 重复 jieba 分词 query，N 次冗余）
    q_terms = _query_terms(q_lower)

    for r in results:
        text = (r.get("content") or r.get("text") or "").lower()
        fn = (r.get("file_name") or "").lower()
        boost = 0

        # 全 query 精确命中
        if q_lower in text:
            boost += 5
        # 关键词命中（中文无空格，使用 jieba 分词；英文回退空格拆分）
        hit = sum(1 for t in q_terms if t in text)
        boost += min(hit, 5)
        # 文件名命中
        if q_lower in fn or any(t in fn for t in q_terms):
            boost += 3
        # 型号精确命中
        for em in exact_models:
            if em in text:
                boost += 3
            if em in fn:
                boost += 5

        r["score"] = round(float(r.get("score", 0)) + boost, 2)

    results.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return results


def dynamic_category_weight(query: str, results: list) -> list:
    """按查询命中分类，对同类文档加权"""
    if not results:
        return results
    q_lower = query.lower()
    cat_weights = {}
    for cat, kws in _CATEGORY_KW.items():
        hits = sum(1 for kw in kws if kw in q_lower)
        if hits > 0:
            cat_weights[cat] = min(hits * 2, 8)
    if not cat_weights:
        return results

    for r in results:
        cat = r.get("category") or ""
        cat = cat if isinstance(cat, str) else str(cat)
        w = cat_weights.get(cat, 0)
        if w > 0:
            r["score"] = round(float(r.get("score", 0)) + w, 2)

    results.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return results


def weighted_rrf_fusion(bm25: list, vec: list, query: str, top_k: int, k: int = 60,
                        graph: list = None) -> list:
    """动态 α 加权的 RRF 融合（替代原硬编码 BM25_WEIGHT/VECTOR_WEIGHT）

    与原 search.py 的 _rrf_fusion 行为一致，但权重由 query 类型动态决定。
    graph: 可选的图谱召回源（第三个召回源），参与 RRF 融合，权重与向量同一档。
    """
    v_w, b_w = get_dynamic_alpha(query)
    g_w = v_w  # 图谱导航与向量语义同级权重

    scores = {}
    details = {}

    for rank, item in enumerate(bm25):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + b_w / (k + rank + 1)
        details[cid] = dict(item)
        details[cid]["_bm25_rank"] = rank

    for rank, item in enumerate(vec):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + v_w / (k + rank + 1)
        if cid not in details:
            details[cid] = dict(item)
        details[cid]["_vector_rank"] = rank

    # 图谱召回源（可选第三源，同向量权重档）
    if graph:
        for rank, item in enumerate(graph):
            if not item:
                continue
            cid = item["id"]
            scores[cid] = scores.get(cid, 0) + g_w / (k + rank + 1)
            if cid not in details:
                details[cid] = dict(item)
            details[cid]["_graph_rank"] = rank

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for cid, score in ranked:
        item = details[cid]
        item["score"] = round(score, 6)
        item["source"] = "bm25+vector"
        item["_alpha"] = {"vector": v_w, "bm25": b_w}
        result.append(item)

    return result


def post_rank(query: str, merged: list) -> list:
    """融合后处理：精确型号加权 + 分类加权（可链式调用）"""
    merged = exact_match_boost(query, merged)
    merged = dynamic_category_weight(query, merged)
    return merged
