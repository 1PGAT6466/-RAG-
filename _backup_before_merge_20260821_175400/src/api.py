"""
API 路由
"""
import os
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from config import UPLOAD_DIR

from src.auth.jwt import register, login
from src.auth.deps import get_current_user, require_admin
from src.auth.rate_limit import login_limiter, register_limiter
from src.retrieval.search import search
from src.chat.engine import generate
from src.storage.db import (
    list_files, get_file, delete_file, update_file_category,
    update_file_tags, update_file_folder, list_folders,
    get_chunks_by_file, get_graph_data, get_file_backlinks,
    list_entities, get_entity, get_entity_chunks, get_entity_files,
    get_entity_spec_params, get_entity_relations,
    get_entity_graph,
)

logger = logging.getLogger("rag.api")
router = APIRouter()


# === 数据模型 ===

class RegisterReq(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, pattern=r'^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$')
    password: str = Field(..., min_length=6, max_length=128)

class LoginReq(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)

class SearchReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=50)

class ChatReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=50)
    mode: str = Field("auto", pattern=r'^(auto|knowledge|chat|web)$')
    history: list[dict] = Field(default_factory=list)
    conversation_id: int | None = None

class CategoryReq(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)

class TagsReq(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=100)

class FolderReq(BaseModel):
    folder: str = Field(..., min_length=1, max_length=512)

class FindFileReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


# === 认证依赖 ===
# 统一从 src.auth.deps 导入 get_current_user（避免多处重复定义）


# === 认证 API ===

@router.post("/api/auth/register")
async def api_register(req: RegisterReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not register_limiter.allow(client_ip):
        raise HTTPException(429, "注册过于频繁，请稍后再试")
    try:
        result = register(req.username, req.password)
        return {"status": "ok", "data": result}
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/api/auth/login")
async def api_login(req: LoginReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(client_ip):
        raise HTTPException(429, "尝试次数过多，请 1 分钟后再试")
    try:
        token = login(req.username, req.password)
        # 从 token 解析 role（或直接查库），返回给前端做角色分流
        from src.auth.jwt import verify_token
        payload = verify_token(token)
        return {"status": "ok", "data": {"token": token, "username": req.username, "role": payload.get("role", "user")}}
    except ValueError as e:
        raise HTTPException(401, str(e))


# === 文档 API ===

@router.post("/api/documents/upload")
async def api_upload(file: UploadFile = File(...), user=Depends(require_admin)):
    # 文件名安全：只取 basename，防路径穿越（../ 或绝对路径）
    from pathlib import Path as _Path
    safe_name = _Path(file.filename or "").name or "upload"
    # 流式写入临时文件（带大小限制，防超大文件耗尽磁盘）
    from config import MAX_UPLOAD_SIZE_MB
    limit_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    tmp = UPLOAD_DIR / f"_tmp_{safe_name}"
    written = 0
    with open(tmp, "wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):
            written += len(chunk)
            if written > limit_bytes:
                f.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"文件过大，上限 {MAX_UPLOAD_SIZE_MB}MB")
            f.write(chunk)
    # 加入引擎流水线，立即返回
    from src.pipeline.engine import enqueue
    task_id = enqueue(str(tmp), safe_name)
    return {"status": "ok", "data": {"task_id": task_id, "filename": safe_name, "message": "已加入处理队列"}}


@router.get("/api/documents/upload/{task_id}/progress")
async def api_upload_progress(task_id: str, user=Depends(get_current_user)):
    from src.pipeline.engine import get_status
    status = get_status(task_id)
    if not status:
        raise HTTPException(404, "任务不存在")
    return {"status": "ok", "data": status}

@router.get("/api/documents")
async def api_list_files(category: str = None, model: str = None, material: str = None, date: str = None, folder: str = None, user=Depends(get_current_user)):
    from src.storage.db import list_files_with_entities
    files = list_files_with_entities(category=category, model=model, material=material, date=date, folder=folder)
    return {"status": "ok", "data": files}

@router.get("/api/folders")
async def api_list_folders(user=Depends(get_current_user)):
    return {"status": "ok", "data": list_folders()}

@router.put("/api/documents/{file_id}/folder")
async def api_move_file(file_id: int, req: FolderReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    update_file_folder(file_id, req.folder)
    return {"status": "ok"}

@router.get("/api/documents/{file_id}")
async def api_get_file(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    chunks = get_chunks_by_file(file_id)
    return {"status": "ok", "data": {"file": f, "chunks": chunks}}

@router.get("/api/documents/{file_id}/backlinks")
async def api_file_backlinks(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    data = get_file_backlinks(file_id)
    return {"status": "ok", "data": data}

@router.get("/api/documents/{file_id}/markdown")
async def api_file_markdown(file_id: int, user=Depends(get_current_user)):
    """将文件所有 chunk 拼接为可读 Markdown（Obsidian 式渲染 .md 而非纯文本）"""
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    chunks = get_chunks_by_file(file_id)
    from src.pipeline.markdown_render import chunks_to_markdown
    md = chunks_to_markdown(f["name"], chunks)
    return {"status": "ok", "data": {"markdown": md, "file_name": f["name"]}}

@router.get("/api/documents/{file_id}/images")
async def api_file_images(file_id: int, user=Depends(get_current_user)):
    """返回文件的图片列表（按页排序），供可读模式穿插 ![[图]] 显示"""
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    from src.storage.db import list_images
    imgs = list_images(file_id)
    return {"status": "ok", "data": imgs}


@router.delete("/api/documents/{file_id}")
async def api_delete_file(file_id: int, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    delete_file(file_id)
    return {"status": "ok"}

@router.put("/api/documents/{file_id}/category")
async def api_set_category(file_id: int, req: CategoryReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    update_file_category(file_id, req.category)
    return {"status": "ok"}

@router.put("/api/documents/{file_id}/tags")
async def api_set_tags(file_id: int, req: TagsReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    update_file_tags(file_id, req.tags)
    return {"status": "ok"}


# === 搜索 API ===

@router.post("/api/search")
async def api_search(req: SearchReq, user=Depends(get_current_user)):
    results = await search(req.query, top_k=req.top_k)
    return {"status": "ok", "data": results}

@router.post("/api/files/find")
async def api_find_files(req: FindFileReq, user=Depends(get_current_user)):
    """AI 智能找文件：用大白话描述 → 语义检索 → 聚合到文件级 + LLM 推荐理由"""
    results = await search(req.query, top_k=30)
    if not results:
        return {"status": "ok", "data": [], "message": "未找到相关文件"}
    # 聚合到文件级：每个文件取最高分 chunk，累计命中数
    file_map = {}
    for r in results:
        fid = r.get("file_id")
        if not fid:
            continue
        if fid not in file_map:
            file_map[fid] = {
                "file_id": fid,
                "file_name": r.get("file_name") or "",
                "score": r.get("score", 0.0),
                "hits": 0,
                "snippets": [],
            }
        entry = file_map[fid]
        entry["hits"] += 1
        entry["score"] = max(entry["score"], r.get("score", 0.0))
        if len(entry["snippets"]) < 2:
            snippet = (r.get("content") or "")[:120]
            if snippet:
                entry["snippets"].append(snippet)
    files = sorted(file_map.values(), key=lambda x: (x["hits"], x["score"]), reverse=True)[:8]
    # LLM 生成一句推荐理由（失败降级空理由）
    reasons = await _generate_find_reasons(req.query, files)
    for i, f in enumerate(files):
        f["reason"] = reasons.get(i, "")
    return {"status": "ok", "data": files}

async def _generate_find_reasons(query: str, files: list[dict]) -> dict:
    """为找文件结果生成推荐理由，失败降级为空（不阻断）"""
    if not files:
        return {}
    try:
        from src.chat.engine import _call_llm_with_fallback
        brief = "\n".join(
            f"{i+1}. {f['file_name']} —— 命中片段：{f['snippets'][0] if f['snippets'] else ''}"
            for i, f in enumerate(files)
        )
        prompt = (
            f"用户想找：「{query}」\n\n"
            f"检索到的候选文件：\n{brief}\n\n"
            f"请为每个文件用一句话（不超过20字）说明为什么它符合用户需求。"
            f"严格输出 JSON 对象，key 为文件名，value 为推荐理由，不要任何额外说明。"
        )
        raw = await _call_llm_with_fallback([
            {"role": "system", "content": "你是文件检索助手，输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ])
        import json as _json, re as _re
        m = _re.search(r"\{.*\}", raw, _re.S)
        if not m:
            return {}
        obj = _json.loads(m.group(0))
        # 映射回索引
        by_name = {f["file_name"]: i for i, f in enumerate(files)}
        out = {}
        for name, reason in obj.items():
            if name in by_name:
                out[by_name[name]] = str(reason)
        return out
    except Exception as e:
        logger.warning(f"找文件推荐理由生成失败（已忽略）: {e}")
        return {}


# === 对话 API ===

@router.post("/api/chat")
async def api_chat(req: ChatReq, user=Depends(get_current_user)):
    try:
        return await _handle_chat(req, user)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.error(f"对话异常: {e}", exc_info=True)
        raise HTTPException(500, "对话服务异常，请稍后重试")


async def _handle_chat(req: ChatReq, user: dict):
    from src.chat.router import classify_intent
    from src.chat.engine import generate, generate_chat, build_citation_sources
    from src.storage.db import (
        add_conversation_message, get_conversation_messages,
        update_conversation_title, get_conversation,
    )

    user_id = user.get("user_id")
    conversation_id = req.conversation_id
    if conversation_id:
        conv = get_conversation(conversation_id, user_id=user_id)
        if not conv:
            raise HTTPException(404, "会话不存在")

    mode = req.mode
    if mode == "auto":
        mode = classify_intent(req.query)
    if mode not in ("knowledge", "chat", "web"):
        mode = "knowledge"

    # 会话历史：传了 conversation_id 且前端未带 history 时，从库读
    req_history = req.history
    if conversation_id and not req.history:
        msgs = get_conversation_messages(conversation_id)
        req_history = [{"role": m["role"], "content": m["content"]} for m in msgs]

    result = None

    if mode == "chat":
        answer = await generate_chat(req.query, history=req_history)
        result = {"answer": answer, "sources": [], "mode": "chat"}

    elif mode == "web":
        from config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            answer = await generate_chat(req.query, history=req_history)
            result = {"answer": "（提示：当前未配置联网搜索，以下为基于模型知识的回答，非实时信息。）\n\n" + answer, "sources": [], "mode": "chat"}
        else:
            from src.chat.engine import generate_web
            from src.chat.web_search import web_search
            web_results = await web_search(req.query, max_results=5)
            if not web_results:
                answer = await generate_chat(req.query, history=req_history)
                result = {"answer": "（联网搜索未能获取结果，以下为基于模型知识的回答。）\n\n" + answer, "sources": [], "mode": "chat"}
            else:
                answer, web_sources = await generate_web(req.query, web_results)
                result = {"answer": answer, "sources": web_sources, "mode": "web"}

    else:
        # Adaptive-RAG: 简单查询跳过检索，直接用 LLM 回答
        from src.chat.router import classify_complexity
        complexity = classify_complexity(req.query)
        if complexity == "simple":
            answer = await generate_chat(req.query, history=req.history)
            result = {"answer": answer, "sources": [], "mode": "chat"}
        else:
            # 语义缓存：相似查询直接返回缓存结果
            from src.chat.cache import lookup as cache_lookup, store as cache_store
            from src.pipeline.embedder import encode_query
            try:
                q_emb = encode_query(req.query)
                cached = cache_lookup(q_emb)
                if cached:
                    result = {"answer": cached["answer"], "sources": cached["sources"], "mode": "knowledge"}
                    if conversation_id:
                        add_conversation_message(conversation_id, "user", req.query, mode="knowledge", sources=[])
                        add_conversation_message(conversation_id, "assistant", result["answer"], mode="knowledge", sources=result.get("sources", []))
                        msgs = get_conversation_messages(conversation_id)
                        if len(msgs) <= 2:
                            title = req.query[:30] + ("..." if len(req.query) > 30 else "")
                            update_conversation_title(conversation_id, title)
                    result["conversation_id"] = conversation_id
                    return {"status": "ok", "data": result}
            except Exception:
                pass

            # Rewrite-Retrieve-Read: 改写查询以提升检索质量
            from src.chat.router import rewrite_query
            search_query = await rewrite_query(req.query)
            results = await search(search_query, top_k=req.top_k)
            if not results and search_query != req.query:
                results = await search(req.query, top_k=req.top_k)
            if not results:
                result = {"answer": "知识库中未检索到与您问题相关的内容，请尝试更换关键词或先上传相关文档。", "sources": [], "mode": "knowledge"}
            else:
                answer, refs = await generate(req.query, results)
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
            try:
                cache_store(req.query, q_emb, result["answer"], result["sources"])
            except Exception:
                pass

    # 会话持久化
    if conversation_id:
        add_conversation_message(conversation_id, "user", req.query, mode=mode, sources=[])
        add_conversation_message(conversation_id, "assistant", result["answer"], mode=result["mode"], sources=result.get("sources", []))
        msgs = get_conversation_messages(conversation_id)
        if len(msgs) <= 2:
            title = req.query[:30] + ("..." if len(req.query) > 30 else "")
            update_conversation_title(conversation_id, title)

    result["conversation_id"] = conversation_id
    return {"status": "ok", "data": result}


@router.post("/api/chat/stream")
async def api_chat_stream(req: ChatReq, user=Depends(get_current_user)):
    """SSE 流式对话：逐 token 推送，前端可实时渲染"""
    from fastapi.responses import StreamingResponse
    from src.chat.engine import generate_stream
    from src.storage.db import get_conversation

    if req.conversation_id:
        conv = get_conversation(req.conversation_id, user_id=user.get("user_id"))
        if not conv:
            raise HTTPException(404, "会话不存在")

    from src.chat.router import rewrite_query
    search_query = await rewrite_query(req.query)
    results = await search(search_query, top_k=req.top_k)
    if not results and search_query != req.query:
        results = await search(req.query, top_k=req.top_k)
    if not results:
        async def empty():
            yield "data: 知识库中未检索到相关内容\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    async def event_stream():
        full_answer = ""
        async for token in generate_stream(req.query, results):
            if token.startswith("\n__SOURCES__"):
                yield f"data: {token}\n\n"
            else:
                full_answer += token
                yield f"data: {token}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# === 会话管理 API ===

class ConversationCreateReq(BaseModel):
    title: str = Field("新对话", max_length=100)

@router.get("/api/conversations")
async def api_list_conversations(user=Depends(get_current_user)):
    from src.storage.db import list_conversations
    return {"status": "ok", "data": list_conversations(user.get("user_id"))}

@router.post("/api/conversations")
async def api_create_conversation(req: ConversationCreateReq, user=Depends(get_current_user)):
    from src.storage.db import create_conversation
    cid = create_conversation(user.get("user_id"), req.title)
    return {"status": "ok", "data": {"id": cid, "title": req.title}}

@router.get("/api/conversations/{conversation_id}")
async def api_get_conversation(conversation_id: int, user=Depends(get_current_user)):
    from src.storage.db import get_conversation, get_conversation_messages
    conv = get_conversation(conversation_id, user_id=user.get("user_id"))
    if not conv:
        raise HTTPException(404, "会话不存在")
    msgs = get_conversation_messages(conversation_id)
    return {"status": "ok", "data": {"conversation": conv, "messages": msgs}}

@router.delete("/api/conversations/{conversation_id}")
async def api_delete_conversation(conversation_id: int, user=Depends(get_current_user)):
    from src.storage.db import get_conversation, delete_conversation
    conv = get_conversation(conversation_id, user_id=user.get("user_id"))
    if not conv:
        raise HTTPException(404, "会话不存在")
    delete_conversation(conversation_id)
    return {"status": "ok"}


# === 知识图谱 API ===

@router.get("/api/graph")
async def api_graph(user=Depends(get_current_user)):
    data = get_graph_data()
    return {"status": "ok", "data": data}


@router.get("/api/entities")
async def api_entities(etype: str = None, user=Depends(get_current_user)):
    """实体列表，可按 type 过滤"""
    entities = list_entities(etype=etype)
    return {"status": "ok", "data": entities}


@router.get("/api/entities/graph")
async def api_entity_graph(etype: str = None, user=Depends(get_current_user)):
    """实体关系图（节点 + 边），可按类型过滤（逗号分隔）"""
    types = [t.strip() for t in etype.split(",") if t.strip()] if etype else None
    data = get_entity_graph(types=types)
    return {"status": "ok", "data": data}


@router.get("/api/entities/{entity_id}")
async def api_entity_detail(entity_id: int, user=Depends(get_current_user)):
    """实体详情 + 反链（chunks + files）+ 规格参数（spec 边）"""
    entity = get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    chunks = get_entity_chunks(entity_id)
    files = get_entity_files(entity_id)
    spec_params = get_entity_spec_params(entity_id)
    relations = get_entity_relations(entity_id)
    return {"status": "ok", "data": {
        "entity": entity, "chunks": chunks, "files": files,
        "spec_params": spec_params, "relations": relations,
    }}


# === 健康检查 ===

@router.get("/api/health")
async def api_health():
    return {"status": "ok", "service": "rag-framework"}


# === 插件 API ===
from src import api_plugins
api_plugins.register(router)

# === MCP 市场 API ===
from src import api_mcp
api_mcp.register(router)



