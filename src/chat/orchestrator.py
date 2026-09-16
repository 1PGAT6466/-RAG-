"""
orchestrator.py — 对话编排引擎
==============================
统一处理所有对话模式（auto/knowledge/chat/web），职责：
  1. 意图分类 → 模式路由（auto 模式下 LLM 判断 chat/knowledge/web）
  2. 语义缓存 → 快速返回（cosine similarity > 阈值直接命中）
  3. 查询改写 → 检索增强（多轮指代消解 + LLM 查询扩展）
  4. LLM 生成 → 引用标注（[1][2] 编号 → sources 映射）
  5. 会话持久化（流式 + 非流式都落库）

关键设计决策：
  - 多轮融合时跳过语义缓存（缓存 key 无法表达有态多轮语境）
  - build_citation_sources 无引用时返回空列表（不制造假象）
  - clean_phantom_citations 洗除越界杜撰编号（[6]但 refs 只 5 条 → 删 [6]）
  - 流式路径通过 _persist_stream 辅助函数落库（SSE 结束后写 user + assistant 两条消息）
"""
import logging
from src.chat.router import classify_intent, classify_intent_async, classify_complexity, rewrite_query
from src.chat.engine import generate, generate_chat, generate_web, build_citation_sources
from src.retrieval.search import search
from src.storage.db import (
    add_conversation_message, get_conversation_messages,
    update_conversation_title, get_conversation,
)

logger = logging.getLogger("rag.chat.orchestrator")


def _query_has_exact_entity(query: str) -> bool:
    """判断 query 是否含「精确实体」（型号/标准号/材料/参数），含则跳过改写。

    背景：改写会把精确实体拆散（如「镀金层」→「镀金」「厚度」「要求」），
    导致 BM25 召回误导（机械手册的镀层表）而非精确命中（Foxconn 的镀金层）。
    对这类精确查询，用原 query 直搜更准，故返回 True 跳过 rewrite。
    """
    if not query:
        return False
    try:
        from src.retrieval.ranking import detect_exact_models
        # 型号/标准号/材料（detect_exact_models 已覆盖 fakra/mini-fakra/材料/尺寸）
        if detect_exact_models(query):
            return True
    except Exception:
        pass
    # 标准号 / 材料 / 参数类精确实体的额外正则
    import re
    q = query.lower()
    standards = r"\b(gb/t|gjb|iso|iec|din|qc/t|jb/t)\s*[-/]?\s*\d+"
    materials = r"\b(lcp|pa66|pa6|pbt|pps|peek|abs|pp|pom)\b"
    # 中文参数/工艺词：不能用 \b（对中文不生效），改直接子串匹配
    params_cn = ("阻抗", "频率", "额定电压", "额定电流", "耐压", "绝缘电阻",
                 "漏电流", "接触电阻", "厚度", "公差", "镀金", "镀层", "材质")
    if re.search(standards, q) or re.search(materials, q):
        return True
    if any(p in q for p in params_cn):
        return True
    return False


# 多轮指代词/省略词（规则式识别，零 LLM），按长度递减排序，匹配时取最长前缀
#   （避免「那」先把「那个」截断成「个」、或「它」先把「它的」截断成「的」这类短词抢长词 bug）
_MULTITURN_REF = tuple(sorted((
    "它", "这", "那", "该", "此", "这个", "那个", "其",
    "它的", "他的", "这个的", "那个的",
), key=len, reverse=True))


def _resolve_multiturn(query: str, history: list[dict]) -> str:
    """多轮 query 融合：把代词/省略指代补全，让 RAG 检索能利用多轮语境。

    规则式（零 LLM）：若当前 query 以指代词（它/这/那/其）开头，或过于简短
    且上一轮是 user 提问，则把上一轮 user query 的关键部分拼接到当前 query。

    返回融合后的 query；若无需融合返回原 query。
    """
    if not query or not history:
        return query
    q = query.strip()
    # 取最近一条 user 消息作为指代锚点
    prev_user = None
    for m in reversed(history):
        if m.get("role") == "user":
            prev_user = (m.get("content") or "").strip()
            break
    if not prev_user:
        return query

    # 1. 指代词开头：直接用 prev 作主语补全
    if any(q.startswith(r) for r in _MULTITURN_REF):
        # 去掉开头的指代词，拼接 prev 的实体部分
        tail = q
        for r in _MULTITURN_REF:
            if tail.startswith(r):
                tail = tail[len(r):]
                # S15: 只去掉一个“的”前缀，而非 lstrip("的") 会误删所有格
                if tail.startswith("的"):
                    tail = tail[1:]
                break
        fused = f"{prev_user} {tail}".strip()
        logger.info(f"多轮融合: '{query[:30]}' -> '{fused[:50]}'")
        return fused

    # 2. 短 query（≤3 字）且非精确实体：可能是省略追问，拼 prev
    if len(q) <= 3 and not _query_has_exact_entity(q):
        fused = f"{prev_user} {q}".strip()
        logger.info(f"多轮融合(短追问): '{q}' -> '{fused[:50]}'")
        return fused

    return query


async def handle_chat(
    query: str,
    user: dict,
    mode: str = "auto",
    top_k: int = 10,
    history: list[dict] = None,
    conversation_id: int = None,
) -> dict:
    """统一对话入口，返回 {answer, sources, mode, conversation_id}"""
    user_id = user.get("user_id")

    # 会话校验
    if conversation_id:
        conv = get_conversation(conversation_id, user_id=user_id)
        if not conv:
            raise ValueError("会话不存在")

    # 模式路由
    if mode == "auto":
        mode = await classify_intent_async(query)
    if mode not in ("knowledge", "chat", "web", "meta"):
        mode = "knowledge"

    # 会话历史：传了 conversation_id 且前端未带 history 时，从库读
    req_history = history
    if conversation_id and not history:
        msgs = get_conversation_messages(conversation_id)
        req_history = [{"role": m["role"], "content": m["content"]} for m in msgs]

    # === 元查询（关于知识库本身的提问） ===
    if mode == "meta":
        result = await _handle_meta(query, history=req_history)

    # === 自由对话 ===
    elif mode == "chat":
        answer = await generate_chat(query, history=req_history)
        result = {"answer": answer, "sources": [], "mode": "chat"}

    # === 联网搜索 ===
    elif mode == "web":
        result = await _handle_web(query, req_history)

    # === 知识库检索（默认） ===
    else:
        result = await _handle_knowledge(query, top_k, history=req_history)

    # 会话持久化
    if conversation_id:
        add_conversation_message(conversation_id, "user", query, mode=mode, sources=[])
        add_conversation_message(conversation_id, "assistant", result["answer"],
                                 mode=result["mode"], sources=result.get("sources", []))
        msgs = get_conversation_messages(conversation_id)
        if len(msgs) <= 2:
            title = query[:30] + ("..." if len(query) > 30 else "")
            update_conversation_title(conversation_id, title)

    result["conversation_id"] = conversation_id
    return result


async def _handle_meta(query: str, history: list[dict] = None) -> dict:
    """元查询模式：关于知识库本身的提问，直接查数据库而非检索。"""
    import sqlite3
    from config import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL")
        file_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM chunks")
        chunk_count = cur.fetchone()[0]
        cur.execute("SELECT name, category, chunk_count FROM files WHERE deleted_at IS NULL ORDER BY id")
        files = cur.fetchall()
        conn.close()

        file_list = "\n".join(
            f"  - {f[0]}（{f[1] or '未分类'}，{f[2]} 个切块）" for f in files
        )
        context = f"知识库共 {file_count} 个文件、{chunk_count} 个切块：\n{file_list}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"参考资料：\n\n{context}\n\n问题：{query}"}
        ]
        from src.chat.engine import _call_llm_with_fallback
        answer = await _call_llm_with_fallback(messages, max_tokens=1024)
        return {"answer": answer, "sources": [], "mode": "meta"}
    except Exception as e:
        logger.error(f"元查询失败: {e}")
        return {"answer": f"查询知识库信息时出错：{e}", "sources": [], "mode": "meta"}


async def _handle_web(query: str, history: list[dict]) -> dict:
    """联网搜索模式"""
    from config import TAVILY_API_KEY
    if not TAVILY_API_KEY:
        answer = await generate_chat(query, history=history)
        return {"answer": "（提示：当前未配置联网搜索，以下为基于模型知识的回答，非实时信息。）\n\n" + answer,
                "sources": [], "mode": "chat"}

    from src.chat.web_search import web_search, filter_by_relevance, refine_web_results
    web_results = await web_search(query, max_results=10)
    if not web_results:
        answer = await generate_chat(query, history=history)
        return {"answer": "（联网搜索未能获取结果，以下为基于模型知识的回答。）\n\n" + answer,
                "sources": [], "mode": "chat"}

    # 1. 关键词相关性过滤（去噪）
    web_results = filter_by_relevance(query, web_results)
    # 2. LLM 摘要提纯（提取关键信息，压缩上下文）
    refined_context = await refine_web_results(query, web_results)

    answer, web_sources = await generate_web(query, web_results, refined_context=refined_context)
    return {"answer": answer, "sources": web_sources, "mode": "web"}


# 对话链路分阶段耗时 profile（模块级，供可观测性/health 端点暴露）
#   结构：{query, total_ms, stages: {rewrite, search, generate, cache}}，与 search 的 profile 对齐。
_last_chat_profile: dict = {}


def get_last_chat_profile() -> dict:
    return _last_chat_profile


async def _handle_knowledge(query: str, top_k: int, history: list[dict] = None) -> dict:
    """知识库检索模式（含 Adaptive-RAG + 语义缓存 + 查询改写 + 多轮融合）"""
    global _last_chat_profile
    import time as _time
    _t0 = _time.perf_counter()
    _stages: dict = {}

    # 多轮 query 融合：代词/省略指代补全（规则式，零 LLM）
    search_query_base = query
    multiturn_fused = False
    if history:
        fused = _resolve_multiturn(query, history)
        if fused != query:
            search_query_base = fused
            multiturn_fused = True
        _stages["multiturn"] = round((_time.perf_counter() - _t0) * 1000, 1)

    # Adaptive-RAG：简单查询跳过检索
    complexity = classify_complexity(query)
    if complexity == "simple":
        answer = await generate_chat(query, history=history)
        return {"answer": answer, "sources": [], "mode": "chat"}

    # 语义缓存（仅非多轮融合查询；多轮融合依赖 history 语境，缓存 key 无法表达有态语境，
    #   强行按原 query 命中会返回别人/别的语境下的答案，造成上下文泄露式错误）
    from src.chat.cache import lookup as cache_lookup, store as cache_store
    from src.pipeline.embedder import encode_query
    q_emb = None
    _t = _time.perf_counter()
    if not multiturn_fused:
        try:
            q_emb = encode_query(query)
            cached = cache_lookup(q_emb)
            if cached:
                return {"answer": cached["answer"], "sources": cached["sources"], "mode": "knowledge"}
        except Exception:
            pass
    _stages["cache"] = round((_time.perf_counter() - _t) * 1000, 1)

    # Rewrite-Retrieve-Read：改写查询提升检索质量（作用在融合后的 query 上）
    #   但「精确实体查询」（含型号/标准号/材料等）跳过改写——改写会把精确实体
    #   拆散（如「镀金层」拆成「镀金」「厚度」「要求」），反会召回误导（机械手册
    #   的镀层表）而非精确命中（Foxconn 的镀金层）。精确查询用原 query 直搜更准。
    _t = _time.perf_counter()
    _has_exact = _query_has_exact_entity(search_query_base)
    if _has_exact:
        search_query = search_query_base
        logger.info(f"精确实体查询，跳过改写: '{search_query_base[:40]}'")
    else:
        search_query = await rewrite_query(search_query_base)
    _stages["rewrite"] = round((_time.perf_counter() - _t) * 1000, 1)

    _t = _time.perf_counter()
    results = await search(search_query, top_k=top_k)
    if not results and search_query != search_query_base:
        results = await search(search_query_base, top_k=top_k)
    _stages["search"] = round((_time.perf_counter() - _t) * 1000, 1)

    if not results:
        _last_chat_profile = {"query": query[:50], "total_ms": round((_time.perf_counter()-_t0)*1000,1), "stages": _stages}
        return {"answer": "知识库中未检索到与您问题相关的内容，请尝试更换关键词或先上传相关文档。",
                "sources": [], "mode": "knowledge"}

    _t = _time.perf_counter()
    answer, refs = await generate(search_query_base if multiturn_fused else query, results)
    _stages["generate"] = round((_time.perf_counter() - _t) * 1000, 1)
    # 引用忠实度校验 + 杜撰编号清洗：回验 LLM 杜撰编号/模糊引用
    #   - 越界编号（[6] 但 refs 只有 5 条）在进前端前被洗除，避免用户点到空脚注
    try:
        from src.chat.engine import check_citation_fidelity, clean_phantom_citations
        fid = check_citation_fidelity(refs, answer)
        if not fid["healthy"]:
            logger.warning(f"引用忠实度告警: 杜撰编号={fid['phantoms']} query='{query[:40]}'")
            answer = clean_phantom_citations(refs, answer)
        if fid["warnings"]:
            logger.warning(f"引用忠实度告警: {fid['warnings']} query='{query[:40]}'")
    except Exception as e:
        logger.debug(f"引用忠实度校验跳过: {e}")
    citations = build_citation_sources(refs, answer)
    result = {
        "answer": answer,
        "sources": [
            {
                "ref": c["ref"],
                "file_id": c.get("file_id"),
                "file_name": c.get("file_name"),
                "chunk_id": c.get("chunk_id"),
                "chunk_index": c.get("chunk_index"),
                "content": c.get("content", "")[:200],
            }
            for c in citations
        ],
        "mode": "knowledge",
    }

    # 缓存成功的 RAG 结果
    if q_emb is not None:
        try:
            cache_store(query, q_emb, result["answer"], result["sources"])
        except Exception:
            pass

    # 写入对话链路分阶段 profile（可观测性，与 search 对齐）
    _stages["total"] = round((_time.perf_counter() - _t0) * 1000, 1)
    _last_chat_profile = {"query": query[:50], "total_ms": _stages["total"], "stages": _stages}
    return result
