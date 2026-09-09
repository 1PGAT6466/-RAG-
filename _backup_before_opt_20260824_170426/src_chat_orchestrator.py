"""
orchestrator.py — 对话编排引擎
==============================
统一处理所有对话模式（auto/knowledge/chat/web），职责：
  1. 意图分类 → 模式路由
  2. 语义缓存 → 快速返回
  3. 查询改写 → 检索增强
  4. LLM 生成 → 引用标注
  5. 会话持久化
"""
import logging
from src.chat.router import classify_intent, classify_complexity, rewrite_query
from src.chat.engine import generate, generate_chat, generate_web, build_citation_sources
from src.retrieval.search import search
from src.storage.db import (
    add_conversation_message, get_conversation_messages,
    update_conversation_title, get_conversation,
)

logger = logging.getLogger("rag.chat.orchestrator")


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
        mode = classify_intent(query)
    if mode not in ("knowledge", "chat", "web"):
        mode = "knowledge"

    # 会话历史：传了 conversation_id 且前端未带 history 时，从库读
    req_history = history
    if conversation_id and not history:
        msgs = get_conversation_messages(conversation_id)
        req_history = [{"role": m["role"], "content": m["content"]} for m in msgs]

    # === 自由对话 ===
    if mode == "chat":
        answer = await generate_chat(query, history=req_history)
        result = {"answer": answer, "sources": [], "mode": "chat"}

    # === 联网搜索 ===
    elif mode == "web":
        result = await _handle_web(query, req_history)

    # === 知识库检索（默认） ===
    else:
        result = await _handle_knowledge(query, top_k)

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


async def _handle_web(query: str, history: list[dict]) -> dict:
    """联网搜索模式"""
    from config import TAVILY_API_KEY
    if not TAVILY_API_KEY:
        answer = await generate_chat(query, history=history)
        return {"answer": "（提示：当前未配置联网搜索，以下为基于模型知识的回答，非实时信息。）\n\n" + answer,
                "sources": [], "mode": "chat"}

    from src.chat.web_search import web_search
    web_results = await web_search(query, max_results=5)
    if not web_results:
        answer = await generate_chat(query, history=history)
        return {"answer": "（联网搜索未能获取结果，以下为基于模型知识的回答。）\n\n" + answer,
                "sources": [], "mode": "chat"}

    answer, web_sources = await generate_web(query, web_results)
    return {"answer": answer, "sources": web_sources, "mode": "web"}


async def _handle_knowledge(query: str, top_k: int) -> dict:
    """知识库检索模式（含 Adaptive-RAG + 语义缓存 + 查询改写）"""
    # Adaptive-RAG：简单查询跳过检索
    complexity = classify_complexity(query)
    if complexity == "simple":
        answer = await generate_chat(query)
        return {"answer": answer, "sources": [], "mode": "chat"}

    # 语义缓存
    from src.chat.cache import lookup as cache_lookup, store as cache_store
    from src.pipeline.embedder import encode_query
    q_emb = None
    try:
        q_emb = encode_query(query)
        cached = cache_lookup(q_emb)
        if cached:
            return {"answer": cached["answer"], "sources": cached["sources"], "mode": "knowledge"}
    except Exception:
        pass

    # Rewrite-Retrieve-Read：改写查询提升检索质量
    search_query = await rewrite_query(query)
    results = await search(search_query, top_k=top_k)
    if not results and search_query != query:
        results = await search(query, top_k=top_k)

    if not results:
        return {"answer": "知识库中未检索到与您问题相关的内容，请尝试更换关键词或先上传相关文档。",
                "sources": [], "mode": "knowledge"}

    answer, refs = await generate(query, results)
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

    return result
