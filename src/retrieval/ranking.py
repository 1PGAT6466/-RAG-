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
# 注意：mini-fakra 与 fakra 是父子关系，必须避免子串重复命中。
#   用 (?<![a-z0-9])fakra(?![a-z0-9]) 避免 fakra 误匹配 fakral 之类派生词；
#   子串去重在 detect_exact_models 里处理（命中 mini-fakra 后剔除裸 fakra）。
_MODEL_PATTERNS = [
    r"mini[\s-]?fakra",                          # Mini-FAKRA / mini fakra（优先，更精确）
    r"(?<![a-z0-9])fakra",                        # 裸 FAKRA（去子重在 detect 中处理）
    r"\b[a-z]{2,5}[\s-]?\d{2,6}([\s-]?\w+)?\b",  # GP-20-150, EP-123, AR1, LSW1
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",   # IP 地址（若涉及网络文档）
    r"\b\d+\.\d+\s?mm\b",                        # 尺寸 3.5mm
    r"\bpom\b", r"\bpa66\b", r"\bpa6\b", r"\babs\b", r"\bpp\b", r"\blcp\b",  # 材料型号
]

# 分类关键词表（工业题材，统一走 src.classification.CATEGORY_DICT 权威词典）
_CATEGORY_KW = CATEGORY_DICT


def detect_exact_models(query: str) -> list:
    """检测 query 中出现的精确型号/编号/材料模式。

    关键约束：长 token（如 mini-fakra）优先，命中后不再额外返回其子串 token（fakra），
    避免同一语义实体被重复计算 boost。
    """
    found = []
    for pat in _MODEL_PATTERNS:
        for m in re.finditer(pat, query, re.IGNORECASE):
            token = m.group(0).strip().lower()
            if token and token not in found:
                found.append(token)
    # 去子串：若已命中更长的 mini-fakra，则移除其内含的裸 fakra（同实体不重复计算）
    if "mini-fakra" in found or "mini fakra" in found:
        found = [t for t in found if t != "fakra"]
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
    """按查询命中分类，对同类文档加权。

    修复：检索结果 chunk 级原无 category 字段，导致本函数 r.get('category')
    永远取不到值、从未生效。现按 file_id 批量补查 category，再按「强指向词
    优先级」加权（如「型号/选型/料号」强指向外购件选型，权重高于「轴承」
    这类通用词指向机械设计）。
    """
    if not results:
        return results
    q_lower = query.lower()

    # 强指向词：这些词出现时，对应分类权重更高（破解「型号 vs 通用件」歧义）
    #   「型号/选型/料号」→ 外购件选型（用户问型号时更可能查选型目录）
    _STRONG_HINTS = {
        "外购件选型": ["型号", "选型", "料号", "供应商", "米思米", "怡合达"],
    }

    cat_weights = {}
    for cat, kws in _CATEGORY_KW.items():
        hits = sum(1 for kw in kws if kw in q_lower)
        if hits > 0:
            cat_weights[cat] = min(hits * 2, 8)
    # 强指向词额外加权
    if "型号" in q_lower or "选型" in q_lower or "料号" in q_lower:
        cat_weights["外购件选型"] = cat_weights.get("外购件选型", 0) + 4
    if not cat_weights:
        return results

    # 批量补查 file_id → category（结果缺 category 字段时）
    need_cat = [r["file_id"] for r in results
                if r.get("file_id") and "category" not in r]
    if need_cat:
        try:
            from src.storage.db import _get_conn
            conn = _get_conn()
            ph = ",".join("?" * len(set(need_cat)))
            rows = conn.execute(
                f"SELECT id, category FROM files WHERE id IN ({ph})",
                tuple(set(need_cat)),
            ).fetchall()
            cat_map = {r["id"]: r["category"] for r in rows}
            for r in results:
                if "category" not in r and r.get("file_id") in cat_map:
                    r["category"] = cat_map[r["file_id"]] or ""
        except Exception as e:
            logger.debug(f"补查 category 失败（跳过分类加权）: {e}")
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
                        graph: list = None, filename: list = None) -> list:
    """动态 α 加权的 RRF 融合（替代原硬编码 BM25_WEIGHT/VECTOR_WEIGHT）

    与原 search.py 的 _rrf_fusion 行为一致，但权重由 query 类型动态决定。
    graph: 可选的图谱召回源（第三个召回源），参与 RRF 融合，权重与向量同一档。
    filename: 可选的文件名召回源（第四个召回源）。文件名命中是强信号，权重取
              max(v_w, b_w)——至少不弱于最强词面/向量源，确保文件名命中的目标文档
              能挤进候选池，不被大文档词频碾压。
    """
    v_w, b_w = get_dynamic_alpha(query)
    g_w = v_w  # 图谱导航与向量语义同级权重
    f_w = max(v_w, b_w)  # 文件名命中 = 强信号，权重不低于最强检索源

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

    # 文件名召回源（可选第四源，强信号高权重）
    if filename:
        for rank, item in enumerate(filename):
            if not item:
                continue
            cid = item["id"]
            scores[cid] = scores.get(cid, 0) + f_w / (k + rank + 1)
            if cid not in details:
                details[cid] = dict(item)
            details[cid]["_filename_rank"] = rank

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
    """融合后处理：只补 file_id→category 字段，不做分数加法。

    理由（系统性重构）：最终排序由重排后的 `_recover_exact_match`（置顶保序）负责，
    rerank 会覆盖融合分。此处在 RRF 分上做魔法数字加法（exact_match_boost +
    dynamic_category_weight）在 rerank 下是无用功（会被覆盖），在 rerank 关闭时
    反而污染排序。故只补 category 字段（供重排使用），分数保持 RRF 原值。
    """
    if not merged:
        return merged
    # 批量补查 file_id → category（下游 _recover_exact_match 的分类强指向需要）
    need_cat = {r["file_id"] for r in merged if r.get("file_id") and "category" not in r}
    if need_cat:
        try:
            from src.storage.db import _get_conn
            conn = _get_conn()
            ph = ",".join("?" * len(need_cat))
            rows = conn.execute(
                f"SELECT id, category FROM files WHERE id IN ({ph})",
                tuple(need_cat),
            ).fetchall()
            cat_map = {r["id"]: r["category"] for r in rows}
            for r in merged:
                if "category" not in r and r.get("file_id") in cat_map:
                    r["category"] = cat_map[r["file_id"]] or ""
        except Exception as e:
            logger.debug(f"post_rank 补查 category 失败（跳过）: {e}")
    return merged
