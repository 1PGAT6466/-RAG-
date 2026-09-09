"""api/chat.py — 对话路由"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src.auth.deps import get_current_user

logger = logging.getLogger("rag.api.chat")
router = APIRouter()


def _sse_data(payload: str) -> str:
    """SSE 安全打包：把 payload 内的换行拆成多行 `data:`（SSE 规范，多行 data 属于同一事件）。

    根因：逐 token 用 f"data: {token}\n\n" 打包时，若 token 内含换行（markdown 标题/列表/JSON），
    会破坏 `data:` 前缀解析，导致换行后的内容被前端 `line.startsWith('data: ')` 误判跳过而丢失。
    拆成连续 `data:` 行后，前端累积拼接可完整还原内容（含换行）。
    """
    if "\n" not in payload:
        return f"data: {payload}\n\n"
    lines = payload.split("\n")
    # 每行一个 data: 前缀，最后补空行分隔（SSE 事件结束标记）
    return "".join(f"data: {ln}\n" for ln in lines) + "\n"


def _persist_stream(req: "ChatReq", answer: str, sources: list, mode: str):
    """流式对话结束后的落库（修复：SSE 流式路径从不持久化对话消息）。

    失败静默降级（仅告警），绝不影响已返回的流。非阻塞地写入 user + assistant 两条消息，
    并在会话为首次（≤2 条消息）时回填标题。sources 含 chunk_id，是「chunk 被引用反查」的数据源。

    注意：此函数在 SSE 流结束后执行，此时请求级 DB 连接已关闭，必须自行创建连接。
    """
    if not req.conversation_id:
        return
    try:
        from src.storage.db import _new_conn, add_conversation_message, update_conversation_title, get_conversation_messages
        from src.storage import db as _db
        # 流结束后请求连接已关闭，临时注入新连接
        conn = _new_conn()
        old_conn = _db._conn_var.get()
        _db.set_conn(conn)
        try:
            add_conversation_message(req.conversation_id, "user", req.query, mode=mode, sources=[])
            add_conversation_message(req.conversation_id, "assistant", answer or "", mode=mode, sources=sources)
            msgs = get_conversation_messages(req.conversation_id)
            if len(msgs) <= 2:
                title = req.query[:30] + ("..." if len(req.query) > 30 else "")
                update_conversation_title(req.conversation_id, title)
        finally:
            _db.set_conn(old_conn)
            conn.close()
    except Exception as e:
        logger.warning(f"流式对话落库失败（已忽略）: {e}")


class ChatReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=50)
    mode: str = Field("auto", pattern=r'^(auto|knowledge|chat|web)$')
    history: list[dict] = Field(default_factory=list, max_length=200)
    conversation_id: int | None = None

    @field_validator('history', mode='before')
    @classmethod
    def _trim_history(cls, v):
        """历史超长时截断为统一口径（CHAT_HISTORY_LIMIT），避免超长会话触发 422 校验错误。

        与 src/chat/engine.py 的 CHAT_HISTORY_LIMIT 对齐，消除「API 层 20 vs 引擎层 6」
        两套截止口径不一致的历史缺陷。
        """
        from src.chat.engine import CHAT_HISTORY_LIMIT
        if isinstance(v, list) and len(v) > CHAT_HISTORY_LIMIT:
            return v[-CHAT_HISTORY_LIMIT:]
        return v

    @field_validator('history')
    @classmethod
    def _validate_history(cls, v):
        """限制 history 结构与长度，防止超长/畸形历史撑爆 prompt、放大 LLM 成本。"""
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("history 元素必须是对象")
            role = item.get("role")
            if role not in ("user", "assistant", "system"):
                raise ValueError("history.role 必须是 user/assistant/system")
            content = item.get("content")
            if content is None or not isinstance(content, str) or len(content) > 4000:
                raise ValueError("history.content 必须是长度不超过 4000 的字符串")
        return v


@router.post("/api/chat")
async def api_chat(req: ChatReq, user=Depends(get_current_user)):
    from src.chat.orchestrator import handle_chat
    try:
        result = await handle_chat(req.query, user, mode=req.mode, top_k=req.top_k,
                                   history=req.history, conversation_id=req.conversation_id)
        return {"status": "ok", "data": result}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.error(f"对话异常: {e}", exc_info=True)
        raise HTTPException(500, "对话服务异常，请稍后重试")


@router.post("/api/chat/stream")
async def api_chat_stream(req: ChatReq, user=Depends(get_current_user)):
    """SSE 流式对话。

    auto 模式下先做意图分类（规则+LLM 快判），分流到 chat/web/knowledge 三种流式：
      - chat: 自由对话流式
      - web: 联网搜索后流式
      - knowledge: 检索 RAG 流式
    否则（用户手动指定 chat/web/knowledge）直接走对应链路。
    """
    from src.storage.db import get_conversation
    from src.chat.router import rewrite_query, classify_intent_async
    from src.retrieval.search import search
    from src.chat.engine import generate_stream, generate_chat, _call_llm_with_fallback

    if req.conversation_id:
        conv = get_conversation(req.conversation_id, user_id=user.get("user_id"))
        if not conv:
            raise HTTPException(404, "会话不存在")

    # auto 模式：先意图分类，纠正「闲聊/联网被误判成知识库检索」
    mode = req.mode
    if mode == "auto":
        mode = await classify_intent_async(req.query)
    if mode not in ("knowledge", "chat", "web"):
        mode = "knowledge"

    # === 闲聊模式：自由对话流式 ===
    if mode == "chat":
        async def chat_stream():
            answer = await generate_chat(req.query, history=req.history)
            yield f"data: {answer}\n\n"
            yield "data: [DONE]\n\n"
            _persist_stream(req, answer, [], "chat")
        return StreamingResponse(chat_stream(), media_type="text/event-stream")

    # === 联网模式：搜索后流式 ===
    if mode == "web":
        from config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            async def web_nokey():
                answer = await generate_chat(req.query, history=req.history)
                yield f"data: （未配置联网搜索，以下为模型回答）\n{answer}\n\n"
                yield "data: [DONE]\n\n"
                _persist_stream(req, answer, [], "chat")
            return StreamingResponse(web_nokey(), media_type="text/event-stream")

        from src.chat.web_search import web_search
        web_results = await web_search(req.query, max_results=5)
        if not web_results:
            async def web_empty():
                answer = await generate_chat(req.query, history=req.history)
                yield f"data: （联网搜索未获取结果，以下为模型回答）\n{answer}\n\n"
                yield "data: [DONE]\n\n"
                _persist_stream(req, answer, [], "chat")
            return StreamingResponse(web_empty(), media_type="text/event-stream")
        from src.chat.engine import generate_web
        async def web_stream():
            answer, web_sources = await generate_web(req.query, web_results)
            yield f"data: {answer}\n\n"
            yield "data: [DONE]\n\n"
            _persist_stream(req, answer, web_sources or [], "web")
        return StreamingResponse(web_stream(), media_type="text/event-stream")

    # === 知识库检索模式 ===
    from src.chat.orchestrator import _query_has_exact_entity
    # 精确实体查询（含型号/标准号/材料/参数）跳过改写——改写会把精确实体拆散
    # （如「镀金层」拆成「镀金」「厚度」「要求」），反会召回误导而非精确命中。
    if _query_has_exact_entity(req.query):
        search_query = req.query
    else:
        search_query = await rewrite_query(req.query)
    results = await search(search_query, top_k=req.top_k)
    if not results and search_query != req.query:
        results = await search(req.query, top_k=req.top_k)
    if not results:
        async def empty():
            yield "data: 知识库中未检索到相关内容\n\n"
            yield "data: [DONE]\n\n"
            _persist_stream(req, "知识库中未检索到相关内容", [], "knowledge")
        return StreamingResponse(empty(), media_type="text/event-stream")

    async def event_stream():
        # 流式过程中累积 answer + sources，流结束后落库（修复历史缺陷：SSE 流式
        #   路径从不持久化，导致刷新后会话侧栏只是空壳、且缺少 chunk 被引用反查的数据）。
        import json as _json
        answer_parts = []
        sources = []
        async for token in generate_stream(req.query, results):
            if isinstance(token, str) and token.startswith("__SOURCES__"):
                try:
                    sources = _json.loads(token[len("__SOURCES__"):])
                except Exception:
                    sources = []
                yield _sse_data(token)
                continue
            answer_parts.append(token if isinstance(token, str) else "")
            yield _sse_data(token)
        yield "data: [DONE]\n\n"
        _persist_stream(req, "".join(answer_parts), sources, "knowledge")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
