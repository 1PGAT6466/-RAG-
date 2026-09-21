"""
检索引擎 — BM25 + 向量混合检索 + 结果融合
=============================================

检索流程：
  1. BM25 全文检索（FTS5 + jieba 分词）
  2. 向量检索（ChromaDB 或 SQLite brute-force）
  3. 图谱召回（实体导航，从 query 提取实体跳转关联 chunk）
  4. RRF 融合（Reciprocal Rank Fusion，动态权重）
  5. 精确匹配恢复（_recover_exact_match：表格/标准号/型号硬匹配穿透）
  6. 反馈惩罚（可选，RAG_FEEDBACK_ENABLE：用户踩过的 chunk 降权）
  7. Rerank（可选，SiliconFlow BGE → DeepSeek LLM → 本地 TF-IDF 降级链）

关键设计决策：
  - BM25 和图谱都无结果时直接返回空（向量 alone 无法判断不相关）
  - _dedup_by_file 后 rank 是去重后真实序号，RRF 量纲已统一
  - 反馈惩罚在融合后、rerank 前执行（只影响 RRF 分，不改变原始召回）
"""
import logging
import heapq
import time
from config import (
    SEARCH_TOP_K, BM25_WEIGHT, VECTOR_WEIGHT, RAG_DYNAMIC_RANKING, RAG_RERANK,
    BM25_RECALL_LIMIT, BM25_PER_FILE, VECTOR_RECALL_LIMIT, GRAPH_RECALL_LIMIT,
    VECTOR_HIGH_CONF_THRESHOLD, CANDIDATE_K_MULTIPLIER, RRF_K, RERANK_TOP_K_MULTIPLIER,
    RAG_FEEDBACK_ENABLE, RAG_FEEDBACK_PENALTY_ALPHA, RAG_FEEDBACK_HALF_LIFE_DAYS,
    RAG_FEEDBACK_THRESHOLD, RAG_FEEDBACK_HARD_DOWN,
)
from src.storage.db import fts_search
from src.pipeline.embedder import encode_query, cosine_similarity

logger = logging.getLogger("rag.retrieval")

# 检索阶段级 profile（最近一次 search 的耗时分布，供性能诊断/前端展示）
# 结构：{query, total_ms, stages: {bm25: ms, vector: ms, graph: ms, fusion: ms, rerank: ms, recover: ms}}
_last_profile: dict = {}


def get_last_profile() -> dict:
    """返回最近一次检索的阶段耗时 profile（供可观测性/诊断用）"""
    return _last_profile


def _use_dynamic_ranking() -> bool:
    """是否启用动态融合权重（Feature Flag，默认关闭可回退原硬编码权重）"""
    return RAG_DYNAMIC_RANKING == "1"


def _use_rerank() -> bool:
    """是否启用 Rerank 精排（Feature Flag，默认开启，检索后精排）"""
    return RAG_RERANK == "1"


def _search_debug_enabled() -> bool:
    """#13：检索调试日志开关（环境变量 SEARCH_DEBUG=1 时开启，默认关）。"""
    import os
    return os.getenv("SEARCH_DEBUG", "0") == "1"


def _use_hyde() -> bool:
    """是否启用 HyDE 兜底（默认关闭，与 LLM 减负战略一致）"""
    from config import RAG_HYDE
    return RAG_HYDE == "1"


def _use_multi_query() -> bool:
    """是否启用 multi-query 改写检索（默认关闭）"""
    from config import RAG_MULTI_QUERY
    return RAG_MULTI_QUERY == "1"


async def _generate_multi_queries(query: str, max_queries: int = 3) -> list[str]:
    """用 LLM 将模糊 query 改写为多个子查询（multi-query 核心）。

    返回 [query1, query2, ...]（不含原始 query），失败返回空列表。
    """
    try:
        from src.chat.engine import _call_llm_with_fallback
        prompt = (
            "你是一个检索改写助手。用户的问题可能模糊或口语化。"
            "请将以下问题改写为 2-3 个不同角度的技术查询，每行一个，不要编号：\n"
            f"{query}"
        )
        resp = await _call_llm_with_fallback([
            {"role": "user", "content": prompt}
        ], max_tokens=200)
        if not resp or len(resp.strip()) < 5:
            return []
        queries = [q.strip() for q in resp.strip().split("\n") if q.strip() and len(q.strip()) > 2]
        # 过滤掉与原始 query 完全相同的
        queries = [q for q in queries if q != query]
        return queries[:max_queries]
    except Exception as e:
        logger.warning(f"multi-query 改写失败: {e}")
        return []


def _route_query(query: str) -> str:
    """Query 路由（显式化）：判断 query 类型，决定最优召回策略（可观测信号）。

    返回：'exact'      —— 精确型号/标准号/材料命中（硬事实，强 FTS/图谱 + 精确置顶）
          'material'   —— 材料/工艺词（图谱语义边扩展有效）
          'semantic'   —— 语义模糊查询（向量语义为主）
          'general'    —— 通用

    说明：当前动态 α（get_dynamic_alpha + weighted_rrf_fusion）已按 query 类型
    隐式分配 BM25/向量权重。本函数将路由「显式化」，写入检索 profile 供可观测，
    并为后续「按路由走专项召回」预留接入点（不改动已验证的融合逻辑）。
    """
    if not query:
        return "general"
    import re
    q_lower = query.lower()
    # 标准号
    if re.search(r"(gb/t|gjb|iso|iec|din|qc/t|jb/t)\s*[-/]?\s*\d+", q_lower):
        return "exact"
    try:
        from src.retrieval.ranking import detect_exact_models
        if detect_exact_models(query):
            return "exact"
    except Exception:
        pass
    # 材料/工艺词（图谱语义边有效）
    _materials = ("lcp", "pa66", "pa6", "pbt", "pps", "peek", "abs", "pp", "pom",
                  "铜合金", "黄铜", "不锈钢", "磷青铜", "材料", "工艺", "镀金", "镀层")
    if any(m in q_lower for m in _materials):
        return "material"
    # 语义模糊（对比/区别/原理/为什么）
    if any(k in q_lower for k in ("区别", "对比", "原理", "为什么", "优缺点", "是什么", "含义")):
        return "semantic"
    # 复合查询（包含多个独立主题，如"A 的 X 和 B 的 Y"）
    if "和" in query and len(query) > 20:
        parts = query.split("和")
        if len(parts) == 2 and len(parts[0].strip()) > 4 and len(parts[1].strip()) > 4:
            return "complex"
    return "general"


async def search(query: str, top_k: int = None, with_rerank: bool = True,
                 collect_detail: bool = False,
                 category: str = None, authority: int = None, folder: str = None,
                 user_id: int = None, user_roles: list[str] = None) -> list[dict]:
    """
    混合检索：BM25 + 向量 → 动态加权 RRF 融合 → 精确/分类加权 → Rerank 精排
    返回 [{id, content, file_name, file_id, score, source}, ...]

    collect_detail=True 时返回「检索明细包」而非结果列表（供 /api/search/debug 调试面板）：
      结构 {route, stages, recalls, merged, final}
    - recalls: 四路召回的原始明细（bm25/vector/graph/filename）
    - merged:  融合后（rerank 前）的候选
    - final:   最终 top_k 结果
    该模式零副作用，不改动主检索语义，仅额外暂存中间结果。
    """
    global _last_profile
    if top_k is None:
        top_k = SEARCH_TOP_K

    # #13（2026-09-21）：检索调试日志默认降为 debug，仅在显式开启 SEARCH_DEBUG 时打。
    # 原为 WARNING 级别，每次检索都打，会淹没真实告警。
    if _search_debug_enabled():
        logger.warning(f"[SEARCH_DEBUG] query={query!r} len={len(query)} top_k={top_k}")

    # 阶段级耗时打点（可观测性地基，零副作用）
    _t0 = time.perf_counter()
    _stages: dict = {}
    _route = _route_query(query)
    _stages["route"] = _route

    # 1. BM25 召回：拉大候选池（limit=200），召回后立即做 per-file 截断，
    #    从根上破解「大文档词频碾压、小文档进不了候选」的问题。
    _t = time.perf_counter()
    bm25_results = _bm25_search(query, limit=BM25_RECALL_LIMIT)
    # 召回层即做文档级均衡：每个 file_id 最多保留 8 个 chunk（排名最高前 8）
    bm25_results = _dedup_by_file(bm25_results, per_file=BM25_PER_FILE)
    _stages["bm25"] = round((time.perf_counter() - _t) * 1000, 1)

    # 2. 向量检索
    _t = time.perf_counter()
    vec_results = _vector_search(query, limit=VECTOR_RECALL_LIMIT)
    _stages["vector"] = round((time.perf_counter() - _t) * 1000, 1)

    # 3. 图谱召回（实体导航，第三个召回源）
    _t = time.perf_counter()
    graph_results = _graph_recall(query, limit=GRAPH_RECALL_LIMIT)
    _stages["graph"] = round((time.perf_counter() - _t) * 1000, 1)

    # 3.5 文件名召回兜底（query 实义词命中文件名时，拉取该文件 chunk）
    #     作用：当目标文档因正文无查询字面命中而在召回层被忽略（超块稀释/同义改写），
    #     文件名是补充强信号，把该文件 chunk 拉进候选，给 rerank/置顶机会。
    _t = time.perf_counter()
    filename_results = _filename_recall(query, limit=GRAPH_RECALL_LIMIT)
    _stages["filename"] = round((time.perf_counter() - _t) * 1000, 1)

    # 3.6 Multi-query：LLM 改写为多个子查询分别检索后合并（默认关）
    if _use_multi_query():
        _t = time.perf_counter()
        alt_queries = await _generate_multi_queries(query)
        if alt_queries:
            _mq_bm25 = []
            _mq_vec = []
            for aq in alt_queries:
                _mq_bm25.extend(_bm25_search(aq, limit=BM25_RECALL_LIMIT // 2))
                _mq_vec.extend(_vector_search(aq, limit=VECTOR_RECALL_LIMIT // 2))
            # 合并到主召回池（去重后再进融合）
            _seen_ids = {r["id"] for r in bm25_results + vec_results}
            for r in _mq_bm25 + _mq_vec:
                if r["id"] not in _seen_ids:
                    _seen_ids.add(r["id"])
                    if r in _mq_bm25:
                        bm25_results.append(r)
                    else:
                        vec_results.append(r)
            _stages["multi_query"] = round((time.perf_counter() - _t) * 1000, 1)
            _stages["mq_queries"] = len(alt_queries)
            logger.info(f"multi-query 改写 {len(alt_queries)} 条, BM25+{len(_mq_bm25)}, Vec+{len(_mq_vec)}")

    # debug 模式：暂存四路召回明细（仅 collect_detail=True 时，零副作用）
    if collect_detail:
        _recalls_detail = {
            "bm25": _summarize_recall(bm25_results, "bm25"),
            "vector": _summarize_recall(vec_results, "vector"),
            "graph": _summarize_recall(graph_results, "graph"),
            "filename": _summarize_recall(filename_results, "filename"),
        }

    # 相关性看门：BM25、图谱、文件名召回均零召回时，判定 query 与知识库词汇零重叠，返回空。
    # 理由：向量检索对任意 query（含纯字母乱码）都会返回 top_k（bge-large 对 ASCII
    # 字母串相似度可达 0.5+），无法独立判无；BM25/FTS 才是词汇级真实命中的可靠判据。
    # 文件名召回同样是词汇级（实义词命中文件名），与图谱并列计入看门，不被向量成空。
    if not bm25_results and not graph_results and not filename_results:
        # HyDE 兜底（默认关）：语义模糊且双零召回时，用 LLM 生成假设答案重新向量检索
        if _use_hyde() and _route == "semantic":
            hyde_results = await _hyde_retrieve(query, top_k=top_k)
            if hyde_results:
                _stages["hyde"] = round((time.perf_counter() - _t0) * 1000, 1)
                _stages["total"] = _stages["hyde"]  # ← Critical fix: 补上 total，避免 KeyError
                _last_profile = {"query": query[:50], "top_k": top_k,
                                 "total_ms": _stages["total"], "stages": _stages}
                logger.info(f"HyDE 兜底召回 {len(hyde_results)} 条: query={query[:40]!r}")
                if collect_detail:
                    return _build_debug_detail(_route, query, _stages, _recalls_detail,
                                               hyde_results, hyde_results, "hyde")
                return hyde_results
        # 向量结果仅保留高置信（score >= 0.65），过滤乱码/低质量 query 的噪声
        high_conf_vec = [r for r in vec_results if r.get("score", 0) >= VECTOR_HIGH_CONF_THRESHOLD]
        if not high_conf_vec:
            logger.info(f"检索无词汇命中且向量置信度不足，返回空: query={query[:50]!r}")
            if collect_detail:
                return _build_debug_detail(_route, query, _stages, _recalls_detail, [], [], "empty")
            return []
        logger.info(f"无词汇命中，保留 {len(high_conf_vec)} 条高置信向量结果: query={query[:50]!r}")
        vec_results = high_conf_vec

    # 4. 融合（动态 α 或原硬编码权重）
    #    中间候选池 top_k*3。文件级去重已在召回层完成（BM25 每文件 ≤8），
    #    融合阶段不再重复去重，避免过度削减候选。
    _t = time.perf_counter()
    _candidate_k = max(top_k * CANDIDATE_K_MULTIPLIER, top_k)
    if _use_dynamic_ranking():
        from src.retrieval.ranking import weighted_rrf_fusion, post_rank
        merged = weighted_rrf_fusion(bm25_results, vec_results, query, _candidate_k,
                                     graph=graph_results, filename=filename_results)
        merged = post_rank(query, merged)
    else:
        merged = _rrf_fusion(bm25_results, vec_results, _candidate_k,
                             graph=graph_results, filename=filename_results)
    _stages["fusion"] = round((time.perf_counter() - _t) * 1000, 1)

    # 4.1 反馈反哺（阶段二 B 方案）：对融合结果按 chunk 点踩惩罚（可选，默认关）
    #     只作用于 RRF 融合分，不改详情；rerank/置顶后续仍会基于被惩罚后的候选。
    #     默认 RAG_FEEDBACK_ENABLE=0 → 直接返回原列表，零副作用。
    if RAG_FEEDBACK_ENABLE == "1" and merged:
        _t = time.perf_counter()
        merged = _apply_feedback_penalty(merged)
        _stages["feedback"] = round((time.perf_counter() - _t) * 1000, 1)

    # debug 模式：融合后、rerank 前的候选快照（供调试面板对比 rerank 前后排名变化）
    _fusion_pre_rerank = merged

    # 4. Rerank 精排（可选）
    #    关键：rerank 时用更大的 top_n（top_k*4），让「分类强指向/文件名命中」的目标
    #    文档有机会进入 rerank 候选而非被 top_n 提前淘汰；rerank 之后再统一截断 + 置顶。
    _t = time.perf_counter()
    if with_rerank and _use_rerank() and merged:
        try:
            from src.retrieval.rerank import rerank
            merged = await rerank(query, merged, top_k=max(top_k * RERANK_TOP_K_MULTIPLIER, top_k))
        except Exception as e:
            logger.warning(f"Rerank 失败，使用融合结果: {e}")
    _stages["rerank"] = round((time.perf_counter() - _t) * 1000, 1)

    # 4.5 精确命中置顶 + 分类/文件名词强指向（置顶保序，非加法，见 _recover_exact_match）
    _t = time.perf_counter()
    if merged and with_rerank:
        try:
            merged = _recover_exact_match(query, merged)
        except Exception as e:
            logger.warning(f"精确命中保护失败（已忽略）: {e}")
    _stages["recover"] = round((time.perf_counter() - _t) * 1000, 1)

    # 4.6 元数据过滤（category/authority/folder）— 在截断前执行，避免过滤后结果不足 top_k
    if category or authority is not None or folder:
        merged = _apply_metadata_filter(merged, category=category, authority=authority, folder=folder)

    # P3: 文件级权限过滤（同样在截断前执行）
    if user_id:
        from src.storage.permissions import get_user_accessible_file_ids
        allowed = get_user_accessible_file_ids(user_id, user_roles)
        if allowed is not None:
            merged = [r for r in merged if r.get("file_id") in allowed]

    # 最终截断到 top_k
    merged = merged[:top_k]

    logger.info(f"检索完成: query='{query[:50]}', results={len(merged)}")

    # 记录阶段级 profile（供可观测性/诊断）
    _stages["total"] = round((time.perf_counter() - _t0) * 1000, 1)
    _last_profile = {"query": query[:50], "top_k": top_k,
                     "total_ms": _stages["total"], "stages": _stages}
    logger.info(f"检索 profile: {_stages}")

    # debug 模式：返回检索明细包（结构化，非结果列表）
    if collect_detail:
        return _build_debug_detail(_route, query, _stages, _recalls_detail,
                                   _fusion_pre_rerank, merged, "normal")

    # 5. Workflow hook：检索后处理（声明式插件，容错不阻断）
    try:
        from src.plugins.hooks import run_hook_async
        hook_result = await run_hook_async("on_search", {
            "query": query,
            "results": merged,
            "top_k": top_k,
        })
        # hook 若返回了新的 results，则采用（否则保持原结果）
        if hook_result.get("ok"):
            altered = hook_result["payload"].get("results")
            if isinstance(altered, list):
                merged = altered
    except Exception as e:
        logger.warning(f"on_search hook 异常（已忽略）: {e}")

    return merged


def _apply_metadata_filter(results: list[dict], category: str = None,
                          authority: int = None, folder: str = None) -> list[dict]:
    """P12: 按元数据过滤检索结果（category/authority/folder）。
    从 chunks JOIN files 取元数据，过滤后保留匹配的 chunks。
    """
    if not results:
        return results

    from src.storage.db import _get_conn
    conn = _get_conn()

    # 收集所有 file_id
    file_ids = list({r["file_id"] for r in results if r.get("file_id")})
    if not file_ids:
        return results

    # 构建 files 元数据查询
    conds = ["id IN (" + ",".join("?" * len(file_ids)) + ")"]
    args = list(file_ids)

    if category:
        conds.append("category = ?")
        args.append(category)
    if authority is not None:
        conds.append("authority >= ?")
        args.append(authority)
    if folder:
        conds.append("(folder = ? OR folder LIKE ?)")
        args.append(folder)
        args.append(folder + "/%")

    rows = conn.execute(
        f"SELECT id FROM files WHERE {' AND '.join(conds)}",
        tuple(args)
    ).fetchall()
    allowed_ids = {r["id"] for r in rows}

    filtered = [r for r in results if r.get("file_id") in allowed_ids]
    if len(filtered) < len(results):
        logger.info(f"P12 元数据过滤: {len(results)} -> {len(filtered)} (category={category}, authority={authority}, folder={folder})")
    return filtered


def _summarize_recall(results: list[dict], source: str, limit: int = 30) -> list[dict]:
    """把召回结果精简为调试面板友好的结构（剥离 embedding 等大字段，截断 content）。

    仅用于 collect_detail 调试输出，不影响主检索返回。每个条目含：
      id / file_id / file_name / chunk_index / score / source / rank 标记 / content 摘要。
    """
    out = []
    for i, r in enumerate(results or []):
        if not isinstance(r, dict):
            continue
        content = r.get("content") or r.get("text") or ""
        item = {
            "id": r.get("id"),
            "file_id": r.get("file_id"),
            "file_name": r.get("file_name", ""),
            "chunk_index": r.get("chunk_index"),
            "score": r.get("score"),
            "source": r.get("source", source),
            "content_preview": content[:160],
        }
        # 透传融合阶段的排名标记（bm25/vector/graph/filename rank + rerank 分数）
        for _k in ("_bm25_rank", "_vector_rank", "_graph_rank", "_filename_rank",
                   "_rerank_score", "_pre_rerank_score", "_rerank_source", "_fn_hits"):
            if _k in r:
                item[_k] = r[_k]
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _build_debug_detail(route: str, query: str, stages: dict, recalls: dict,
                        fusion: list, final: list, kind: str) -> dict:
    """组装检索调试明细包（统一 collect_detail=True 时的返回结构）。

    kind 标识检索走向：'normal'（完整链路）、'hyde'（HyDE 兑底）、'empty'（无结果）。
    """
    return {
        "route": route,
        "kind": kind,
        "query": query[:200],
        "stages": stages,
        "recalls": recalls,
        "fusion": _summarize_recall(fusion, "fusion"),
        "final": _summarize_recall(final, f"final(route={route})"),
    }


def _graph_recall(query: str, limit: int = 30) -> list[dict]:
    """图谱召回（实体导航），失败降级空列表"""
    try:
        from src.retrieval.graph_recall import graph_recall
        return graph_recall(query, limit=limit)
    except Exception as e:
        logger.warning(f"图谱召回失败: {e}")
        return []


# 文件名召回的停用词集合：这些词即使命中文件名也不构成强信号（虚词/通用动作词）
_FILENAME_STOPWORDS = {
    "如何", "怎么", "哪里", "什么", "在哪", "功能", "说明", "介绍",
    "使用", "操作", "流程", "模块", "方法", "步骤", "怎样", "为何",
    "为什么", "设置", "配置", "管理", "新建", "在", "的", "与", "和",
    "了", "是", "有", "请", "一个", "一下", "进行", "相关", "需要",
}


def _filename_recall(query: str, limit: int = 30) -> list[dict]:
    """文件名召回兜底：query 的实义词命中文件「文件名」时，直接拉取该文件 chunk 进候选。

    背景（数据问题而非算法缺陷的根治手段）：
      部分目标文档正文里没有查询词的字面形态（如「系统参数设置」文档正文叫「系统设置/通用
      设置」、或超大表格块把唯一命中词「导轨」稀释到 1/4507），导致 BM25/向量在召回层就
      把目标文档整个忽略，后续 rerank / _recover_exact_match 无米下锅。
      而文件名是用户命名时的强语义信号（「(11)--系统参数设置.docx」），命中即该文件高度相关。

    机制：
      1. jieba 分词 query → 去停用词 → 实义词
      2. 对 files.name 做 LIKE 匹配（任一实义词命中即算文件命中）
      3. 命中文件按「命中词数」排序，拉取其全部 chunk（带 source=filename、_fn_hits 标记）
    纯规则 + 零 LLM + 零网络，异常降级空列表。
    """
    if not query:
        return []
    try:
        from src.retrieval.ranking import _query_terms
        from src.storage.db import _get_conn, get_chunks_by_file
    except Exception:
        return []

    # 实义词（jieba 分词去停用词，长度 >= 2）
    terms = [t for t in _query_terms(query.lower()) if t and len(t) >= 2 and t not in _FILENAME_STOPWORDS]
    if not terms:
        return []

    conn = _get_conn()
    # 用任一实义词对文件名做 LIKE 匹配（转义 % _ 通配符，避免注入）
    like_clauses = []
    like_args = []
    for t in terms:
        esc = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_clauses.append("name LIKE ? ESCAPE '\\'")
        like_args.append(f"%{esc}%")
    sql = "SELECT id, name, category, chunk_count FROM files WHERE " + " OR ".join(like_clauses)
    rows = conn.execute(sql, tuple(like_args)).fetchall()

    # 分类强指向兜底：query 含「选型/型号/料号」等强指向词，但未命中任何文件名时，
    #   命中「外购件选型」分类的文件（选型目录）。这些文件（如标准件新表）往往是
    #   超大表格，正文里的关键词（导轨）被大量型号淹没，BM25/向量都排不上；但分类
    #   标签「外购件选型」正是用户问「选型/型号」时的目标。命中词数记为 0.5（弱于
    #   文件名直接命中，但足以把这些 chunk 拉进候选池）。
    cat_rows = []
    cat_ids = set()
    if not rows:
        _q = query.lower()
        if any(k in _q for k in ("选型", "型号", "料号", "外购件", "米思米", "怡合达")):
            cat_rows = conn.execute(
                "SELECT id, name, category, chunk_count FROM files WHERE category = ?",
                ("外购件选型",)
            ).fetchall()
            cat_ids = {r["id"] for r in cat_rows}

    rows = list(rows) + list(cat_rows)
    if not rows:
        return []

    # 按命中词数排序（命中越多越相关），命中词数相同的文件较小者优先（小文件目标更精确）
    #     第三键用文件 id 保证元组可比较（sqlite3.Row 不可比较，会导致 sort 报错）
    #     分类兜底命中的文件 hits 记为 0（不如文件名直接命中），靠分类类型区分排序。
    scored = []
    name_by_id = {}
    for r in rows:
        name_l = (r["name"] or "").lower()
        hits = sum(1 for t in terms if t in name_l)
        if hits <= 0:
            # 分类兜底命中：hits=0 但保留（来源标记 category）
            if r["id"] in cat_ids:
                hits = 0
            else:
                continue
        cn = r["chunk_count"] if "chunk_count" in r.keys() else 0
        scored.append((hits, -cn, r["id"]))
        name_by_id[r["id"]] = r["name"]
    scored.sort(key=lambda x: (x[0], x[1], -x[2]), reverse=True)

    results = []
    seen = set()
    for hits, _, fid in scored:
        # 先截断每个文件的 chunk 数，避免大文件（1500+ chunks）一次性全量拉取
        file_chunks = get_chunks_by_file(fid)[:10]
        for c in file_chunks:
            cid = c["id"]
            if cid in seen:
                continue
            seen.add(cid)
            results.append({
                "id": cid,
                "content": c["content"] or "",
                "file_id": fid,
                "chunk_index": c.get("chunk_index"),
                "file_name": name_by_id[fid],
                "score": float(hits),  # 命中词数作为召回分（量纲与 RRF 无关，仅排序用）
                "source": "filename",
                "_fn_hits": hits,
            })
        if len(results) >= limit:
            break
    if results:
        logger.info(f"文件名召回兜底: query 命中 {len(scored)} 个文件, 返回 {len(results)} chunks")
    return results[:limit]


async def _hyde_retrieve(query: str, top_k: int = 10) -> list[dict]:
    """HyDE 兜底：用 LLM 生成假设答案，再对假设答案做向量检索（LLM 增强召回）。

    仅在「语义模糊 + BM25/图谱双零召回」时启用（RAG_HYDE=1 时才调用），
    作为最终兜底而非主链路，默认关。失败静默降级返回 []。
    """
    try:
        from src.chat.engine import _call_llm_with_fallback
        hypo = await _call_llm_with_fallback([
            {"role": "system", "content": "你是一个工业知识库助手。请写一段约 100 字的回答，说明该问题可能涉及的工业知识要点，无需引用任何文档。"},
            {"role": "user", "content": query},
        ], max_tokens=200)
        if not hypo or len(hypo.strip()) < 10:
            return []
        # 用假设答案重新做向量检索
        return _vector_search(hypo.strip(), limit=top_k)
    except Exception as e:
        logger.warning(f"HyDE 兜底失败（已忽略）: {e}")
        return []


def _bm25_search(query: str, limit: int = 30) -> list[dict]:
    """FTS5 BM25 搜索"""
    try:
        return fts_search(query, limit=limit)
    except Exception as e:
        logger.warning(f"BM25 搜索失败: {e}")
        return []


def _vector_search(query: str, limit: int = 30) -> list[dict]:
    """向量相似度搜索（ChromaDB，回退 SQLite 暴力扫描）"""
    try:
        q_vec = encode_query(query)

        from src.storage.chroma_store import _use_chroma
        if _use_chroma():
            from src.storage.chroma_store import search as chroma_search
            results = chroma_search(q_vec, top_k=limit)
            # 补齐 file_name
            return _attach_file_names(results)
        return _vector_search_sqlite(q_vec, limit)
    except Exception as e:
        logger.warning(f"向量搜索失败: {e}")
        return []


def _attach_file_names(results: list[dict]) -> list[dict]:
    """为结果补充 file_name 与完整 content（Chroma 只存了 file_id/chunk_index）。

    向量存储不再存 content（见 chroma_store，缺陷9：避免 [:500] 截断导致长 chunk
    精确命中/rerank 失效），此处从 chunks 主表按 id 批量回填完整 content，
    并顺带补齐 file_name（一次 SQEET 查询，避免 N+1）。
    """
    if not results:
        return results
    from src.storage.db import _get_conn
    conn = _get_conn()
    chunk_ids = [r["id"] for r in results]
    c_ph = ",".join("?" * len(chunk_ids))
    # 主表按 id 批量回填 content
    rows = conn.execute(
        f"SELECT id, content, file_id FROM chunks WHERE id IN ({c_ph})",
        tuple(chunk_ids),
    ).fetchall()
    content_map = {r["id"]: r["content"] for r in rows}
    file_id_map = {r["id"]: r["file_id"] for r in rows}

    file_ids = set(r["file_id"] for r in results)
    f_ph = ",".join("?" * len(file_ids))
    f_rows = conn.execute(
        f"SELECT id, name FROM files WHERE id IN ({f_ph})",
        tuple(file_ids),
    ).fetchall()
    name_map = {r["id"]: r["name"] for r in f_rows}

    for r in results:
        r["content"] = content_map.get(r["id"], r.get("content", ""))
        r["file_name"] = name_map.get(r["file_id"], "")
        if not r["file_name"]:
            logger.warning(f"孤立 chunk: file_id={r['file_id']} 在 files 表中不存在")
    return results


def _vector_search_sqlite(q_vec: bytes, limit: int = 30) -> list[dict]:
    """SQLite 暴力扫描（回退路径，数据量小时可用）"""
    from src.storage.db import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT c.id, c.content, c.file_id, c.chunk_index, c.embedding, f.name as file_name "
        "FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.embedding IS NOT NULL"
    ).fetchall()

    from src.pipeline.embedder import cosine_similarity
    results = []
    for row in rows:
        if row["embedding"]:
            sim = cosine_similarity(q_vec, row["embedding"])
            if sim > 0.3:  # 阈值过滤
                results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "file_id": row["file_id"],
                    "chunk_index": row["chunk_index"],
                    "file_name": row["file_name"],
                    "score": float(sim),
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def _dedup_by_file(results: list[dict], per_file: int = 3) -> list[dict]:
    """文件级去重：每个 file_id 最多保留 per_file 个 chunk，保证结果文件多样性。

    背景：大文档（如机械手册 1585 chunk）在 BM25 召回时词频碾压，会占满 top 候选，
    而目标小文档（如外购件表 17 chunk）的少数命中 chunk 被挤出。强制每个文件
    最多保留排名最靠前的 per_file 个 chunk，给其他文件留出重排机会。
    保持原排序（已按 score 降序），去重后不重排。
    """
    if not results:
        return results
    seen_count = {}
    out = []
    orphans = []  # 缺 file_id 的异常结果（正常流程不会出现），收集后统一追加，不破坏均衡
    for r in results:
        fid = r.get("file_id")
        if fid is None:
            orphans.append(r)
            continue
        cnt = seen_count.get(fid, 0)
        if cnt < per_file:
            out.append(r)
            seen_count[fid] = cnt + 1
    # 孤儿结果追加在末尾（保守：不参与均衡，但也不无限放行到前列）
    out.extend(orphans)
    return out


def _apply_feedback_penalty(results: list[dict]) -> list[dict]:
    """阶段二 B 方案：对融合结果按「点踩惩罚因子」降低 RRF 融合分（温和、可逆、有衰减）。

    - 只乘 results 里每个 chunk 的 score（RRF 融合分），不改其他字段。
    - 惩罚因子 penalty ∈ (0,1]，由 feedback.get_chunk_penalties 按「时间衰减有效点踩数」算出。
    - 达硬阈值（≥ RAG_FEEDBACK_HARD_DOWN）的 chunk 额外打 `_feedback_hard_down=True` 标记，
      供 _recover_exact_match 判断是否穿透「精确型号/authority」置顶。
    - 未命中的 chunk 不惩罚（penalty=1.0）。
    - 容错：任何异常静默降级为原列表，绝不阻断检索。
    """
    if not results:
        return results
    try:
        from src.storage.feedback import get_chunk_penalties
        cids = {r["id"] for r in results if r.get("id") is not None}
        if not cids:
            return results
        penalties = get_chunk_penalties(
            cids,
            half_life_days=RAG_FEEDBACK_HALF_LIFE_DAYS,
            threshold=RAG_FEEDBACK_THRESHOLD,
            alpha=RAG_FEEDBACK_PENALTY_ALPHA,
        )
        if not penalties:
            return results
        for r in results:
            cid = r.get("id")
            p = penalties.get(cid)
            if p is not None and p < 1.0:
                r["_pre_feedback_score"] = r.get("score", 0)
                r["score"] = round(float(r.get("score", 0)) * p, 6)
                # 硬阈值标记：需查原始有效点踩数（get_chunk_penalties 只返回 penalty），
                # 这里用 penalty 反推近似判断（penalty 越小说明点踩越多），保守打标。
                # 精确判断依赖 hard_down：penalty <= 1/(1+alpha*hard_down) 时视为穿透。
                hard_limit = 1.0 / (1.0 + RAG_FEEDBACK_PENALTY_ALPHA * RAG_FEEDBACK_HARD_DOWN)
                if p <= hard_limit:
                    r["_feedback_hard_down"] = True
        logger.info(f"反馈惩罚：{len(penalties)} 个 chunk 被降权")
    except Exception as e:
        logger.warning(f"反馈惩罚失败（已忽略）: {e}")
    return results


def _recover_exact_match(query: str, results: list[dict]) -> list[dict]:
    """精确命中置顶：rerank 语义重排后，把「型号/编号/文件名词精确命中」的结果置顶。

    背景：rerank（SiliconFlow BGE）只理解语义，不理解精确型号/文件名/标准号。
    精确型号命中是「硬事实」（型号对上了就是对的），语义相似是「软判断」，
    硬事实不适合用加法跟软判断竞争（量纲不匹配、会顶偏）。

    故本函数改为「置顶保序」而非「加分」：
      - 强精确命中（型号/标准号在内容或文件名中命中）→ 置顶，内部保持 rerank 序
      - 分类强指向（query 含「型号/选型/料号」→ 外购件选型类）→ 次优先
    不修改任何结果的 score 数值，只调整顺序，杜绝跷跷板。
    """
    if not results or not query:
        return results
    import re as _re
    try:
        from src.retrieval.ranking import detect_exact_models, _query_terms
    except Exception:
        return results

    q_lower = query.lower().strip()
    exact_models = detect_exact_models(query)
    q_terms = [t for t in _query_terms(q_lower) if t and len(t) >= 2]

    # 批量补查 file_id → category / authority / doc_kind（分类强指向 + 权威置顶需要）
    need_meta = {r["file_id"] for r in results if r.get("file_id") and "authority" not in r}
    if need_meta:
        try:
            from src.storage.db import _get_conn
            conn = _get_conn()
            ph = ",".join("?" * len(need_meta))
            rows = conn.execute(
                f"SELECT id, category, doc_kind, authority FROM files WHERE id IN ({ph})",
                tuple(need_meta),
            ).fetchall()
            meta_map = {r["id"]: r for r in rows}
            for r in results:
                if r.get("file_id") in meta_map:
                    m = meta_map[r["file_id"]]
                    if "category" not in r:
                        r["category"] = m["category"] or ""
                    r["authority"] = m["authority"] or 0
                    r["doc_kind"] = m["doc_kind"] or ""
        except Exception as e:
            logger.debug(f"_recover_exact_match 补查 meta 失败: {e}")

    def _token_in(token: str, text: str) -> bool:
        """词边界匹配：token 前后不能是字母/数字，避免子串误命中。

        注意：不再对裸 fakra 做 mini 前缀排除。历史原因：曾为避免「mini-fakra 与 fakra
        子串重复命中」而加了 mini 排除，但那反而在「查询 = 裸 FAKRA」时误伤了
        Mini-FAKRA 产线工艺文档（其正文主体就是 Mini-Fakra，正是权威来源）。
        实际上 query 含 mini-fakra 时 detect_exact_models 返回的是 ['mini-fakra']（含 mini
        前缀的长 token），不会用裸 'fakra' 去匹配它；而 query 是裸 fakra 时，Mini-FAKRA
        文档恰恰是最相关的权威工艺文档，必须放行。故在此用极简词边界匹配即可。
        """
        return _re.search(r"(?<![a-z0-9])" + _re.escape(token) + r"(?![a-z0-9])", text) is not None

    # 分类强指向：query 含「型号/选型/料号」→ 外购件选型优先
    category_priority = ""
    if "型号" in q_lower or "选型" in q_lower or "料号" in q_lower:
        category_priority = "外购件选型"

    # 领域强指向：query 含某题材领域词 → 该题材文档在同权威等级下优先。
    #   精选高区分度领域词（非整本 CATEGORY_DICT——那里含「标准/规格/材料/公差」等
    #   宽泛词，会跨领域误命中，如「接触电阻标准」里的「标准」误命中「标准件」）。
    #   背景：「镀金层厚度要求」中镀金/镀层是连接器/材料专项，非机械设计，但机械手册
    #   词频碾压，故用领域词把连接器/材料文档置顶。
    domain_priority_cats = set()
    _DOMAIN_HINTS = {
        "连接器": ["连接器", "端子", "接插件", "fakra", "板端", "线端", "镀金", "镀层", "电镀",
                    "插拔", "压接", "接触电阻", "绝缘电阻", "耐压", "漏电流"],
        "材料选型": ["材料", "材质", "镀层", "电镀", "lcp", "pa66", "pa6", "pbt", "pps", "peek",
                      "铜合金", "黄铜", "磷青铜", "不锈钢", "导电率", "介电常数"],
        "工艺规程": ["工艺", "工序", "装配", "检测", "产线", "sop", "注塑", "焊锡", "组装", "压合", "点胶"],
        "电气自动化": ["plc", "伺服", "变频器", "传感器", "接线", "hmi", "阻抗", "高频", "屏蔽", "耐压"],
    }
    for cat, kws in _DOMAIN_HINTS.items():
        if any(kw in q_lower for kw in kws):
            domain_priority_cats.add(cat)

    # 计算每个结果的优先级（离散等级，非加法）
    #   3 = 型号/标准号精确命中 且 高权威文档（工艺规程/技术手册/设计规范，authority>=4）
    #   2 = 型号/标准号精确命中（普通权威，含采购流水/低权威文档）
    #   1 = 分类强指向命中 / 文件名词命中
    #   0 = 普通
    #
    # 权威等级（authority）的作用：当精确型号同时命中「权威工艺手册」和「采购流水」时，
    #   权威手册应予更强的置顶——采购流水虽含该型号字样，但只是「记录买过」，
    #   规格/工艺的权威定义在技术手册里。这是专属化工业检索精准度的关键。
    def _priority(r: dict) -> int:
        # 硬阈值穿透：点踩有效次数 ≥ RAG_FEEDBACK_HARD_DOWN 的 chunk，不给它置顶优先级
        #   （用户多次明确反对该 chunk 的硬事实，如「型号参数是错的」），直接降到 0。
        #   _feedback_hard_down 标记由 _apply_feedback_penalty 在融合阶段打好。
        if r.get("_feedback_hard_down"):
            return 0
        text = (r.get("content") or r.get("text") or "").lower()
        fn = (r.get("file_name") or "").lower()
        authority = int(r.get("authority") or 0)
        if exact_models:
            # 型号在内容或文件名中词边界命中（最强）
            if any(_token_in(em, text) for em in exact_models):
                return 3 if authority >= 4 else 2
            if any(_token_in(em, fn) for em in exact_models):
                return 3 if authority >= 4 else 2
        # 文件名词命中（OA 手册类关键）：query 的实义词命中文件名 → 强信号
        #   停用词集合：排除「如何/怎么/哪里/什么/功能/说明/介绍/使用/操作/在哪」等虚词
        _STOPWORDS = {"如何", "怎么", "哪里", "什么", "在哪", "功能", "说明", "介绍",
                      "使用", "操作", "设置", "配置", "管理", "新建", "流程", "模块",
                      "方法", "步骤", "怎样", "为何", "为什么"}
        if q_terms:
            content_words = [t for t in q_terms if t not in _STOPWORDS]
            if content_words and any(_token_in(t, fn) for t in content_words):
                return 1
        if category_priority:
            cat = r.get("category") or ""
            if cat == category_priority:
                return 1
        # 领域强指向：同权威等级下，连接器/材料文档优于机械设计手册（领域相关决胜负）
        if domain_priority_cats:
            cat = r.get("category") or ""
            if cat in domain_priority_cats:
                return 1
        return 0

    # 稳定排序：先按优先级降序（精确命中置顶），同优先级保持 rerank 原序
    indexed = list(enumerate(results))
    indexed.sort(key=lambda t: (-_priority(t[1]), t[0]))
    return [r for _, r in indexed]


def _rrf_fusion(bm25: list[dict], vec: list[dict], top_k: int, k: int = None,
                graph: list[dict] = None, filename: list[dict] = None) -> list[dict]:
    """RRF 融合 + 去重（固定权重版本，RAG_DYNAMIC_RANKING=0 时使用）。

    与 weighted_rrf_fusion（动态 α 版）唯一区别是权重固定为 BM25_WEIGHT/VECTOR_WEIGHT。
    但必须同样消费 graph / filename 两个召回源——否则关闭动态开关时文件名/图谱
    召回会被静默丢弃（历史缺陷：旧实现只吃 bm25+vec）。
    graph 权重与向量同档；filename 是强信号，权重取 max(BM25_WEIGHT, VECTOR_WEIGHT)。
    """
    if k is None:
        k = RRF_K
    scores = {}
    details = {}

    # BM25 排名
    for rank, item in enumerate(bm25):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + BM25_WEIGHT / (k + rank + 1)
        if cid not in details:
            details[cid] = dict(item)  # 复制，避免共享引用污染上游数据

    # 向量排名
    for rank, item in enumerate(vec):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0) + VECTOR_WEIGHT / (k + rank + 1)
        if cid not in details:
            details[cid] = dict(item)

    # 图谱召回（第三源，权重与向量同档）
    g_w = VECTOR_WEIGHT
    if graph:
        for rank, item in enumerate(graph):
            cid = item["id"]
            scores[cid] = scores.get(cid, 0) + g_w / (k + rank + 1)
            if cid not in details:
                details[cid] = dict(item)

    # 文件名召回（第四源，强信号，权重不低于最强源）
    f_w = max(BM25_WEIGHT, VECTOR_WEIGHT)
    if filename:
        for rank, item in enumerate(filename):
            cid = item["id"]
            scores[cid] = scores.get(cid, 0) + f_w / (k + rank + 1)
            if cid not in details:
                details[cid] = dict(item)

    # 排序取 top_k
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for cid, score in ranked:
        item = details[cid]
        item["score"] = score
        item["source"] = "bm25+vector"
        result.append(item)

    return result
