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
    update_file_tags, get_chunks_by_file, get_graph_data,
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
    # 对话模式：auto(自动路由) / knowledge(仅RAG) / chat(自由对话) / web(联网)
    mode: str = Field("auto")
    # 多轮历史（可选）：[{role, content}, ...]，用于闲聊模式上下文
    history: list[dict] = Field(default_factory=list)

class CategoryReq(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)

class TagsReq(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=100)


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
async def api_list_files(category: str = None, model: str = None, material: str = None, date: str = None, user=Depends(get_current_user)):
    from src.storage.db import list_files_with_entities
    files = list_files_with_entities(category=category, model=model, material=material, date=date)
    return {"status": "ok", "data": files}

@router.get("/api/documents/{file_id}")
async def api_get_file(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise HTTPException(404, "文件不存在")
    chunks = get_chunks_by_file(file_id)
    return {"status": "ok", "data": {"file": f, "chunks": chunks}}

@router.delete("/api/documents/{file_id}")
async def api_delete_file(file_id: int, user=Depends(require_admin)):
    delete_file(file_id)
    return {"status": "ok"}

@router.put("/api/documents/{file_id}/category")
async def api_set_category(file_id: int, req: CategoryReq, user=Depends(require_admin)):
    update_file_category(file_id, req.category)
    return {"status": "ok"}

@router.put("/api/documents/{file_id}/tags")
async def api_set_tags(file_id: int, req: TagsReq, user=Depends(require_admin)):
    update_file_tags(file_id, req.tags)
    return {"status": "ok"}


# === 搜索 API ===

@router.post("/api/search")
async def api_search(req: SearchReq, user=Depends(get_current_user)):
    results = await search(req.query, top_k=req.top_k)
    return {"status": "ok", "data": results}


# === 对话 API ===

@router.post("/api/chat")
async def api_chat(req: ChatReq, user=Depends(get_current_user)):
    from src.chat.router import classify_intent
    from src.chat.engine import generate, generate_chat, build_citation_sources

    # 确定本次对话的模式
    mode = req.mode
    if mode == "auto":
        mode = classify_intent(req.query)

    # 合法模式白名单
    if mode not in ("knowledge", "chat", "web"):
        mode = "knowledge"

    # 闲聊模式：不检索，直接自由对话
    if mode == "chat":
        answer = await generate_chat(req.query, history=req.history)
        return {
            "status": "ok",
            "data": {
                "answer": answer,
                "sources": [],
                "mode": "chat",
            }
        }

    # 联网模式：Tavily 搜索 + LLM 综合
    if mode == "web":
        from config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            # 未配置联网搜索：降级为闲聊，并明确告知用户
            answer = await generate_chat(req.query, history=req.history)
            hint = "（提示：当前未配置联网搜索，以下为基于模型知识的回答，非实时信息。）"
            return {
                "status": "ok",
                "data": {
                    "answer": hint + "\n\n" + answer,
                    "sources": [],
                    "mode": "chat",
                }
            }
        # 联网搜索 + LLM 综合
        from src.chat.engine import generate_web
        from src.chat.web_search import web_search
        web_results = await web_search(req.query, max_results=5)
        if not web_results:
            # 搜索失败/空：降级闲聊
            answer = await generate_chat(req.query, history=req.history)
            return {
                "status": "ok",
                "data": {
                    "answer": "（联网搜索未能获取结果，以下为基于模型知识的回答。）\n\n" + answer,
                    "sources": [],
                    "mode": "chat",
                }
            }
        answer, web_sources = await generate_web(req.query, web_results)
        return {
            "status": "ok",
            "data": {
                "answer": answer,
                "sources": web_sources,
                "mode": "web",
            }
        }

    # knowledge 模式（默认）：检索 + 带引用生成
    results = await search(req.query, top_k=req.top_k)
    if not results:
        return {
            "status": "ok",
            "data": {
                "answer": "知识库中未检索到与您问题相关的内容，请尝试更换关键词或先上传相关文档。",
                "sources": [],
                "mode": "knowledge",
            }
        }
    answer, refs = await generate(req.query, results)
    citations = build_citation_sources(refs, answer)
    return {
        "status": "ok",
        "data": {
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
    }


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



