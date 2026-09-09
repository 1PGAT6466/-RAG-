"""api/documents.py — 文档管理路由"""
import logging
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel, Field
from config import UPLOAD_DIR

from src.api.errors import BizError, ErrorCode
from src.auth.deps import get_current_user, require_admin
from src.storage.db import (
    list_files, get_file, delete_file, update_file_category,
    update_file_tags, update_file_folder, list_folders,
    get_chunks_by_file, get_file_backlinks, list_images, list_files_with_entities,
)

logger = logging.getLogger("rag.api.documents")
router = APIRouter()


class CategoryReq(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)

class TagsReq(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=100)

class FolderReq(BaseModel):
    folder: str = Field(..., min_length=1, max_length=512)


@router.post("/api/documents/upload")
async def api_upload(
    file: UploadFile = File(...),
    folder_id: int = Form(None),
    user=Depends(require_admin),
):
    """上传文档：写入 SeedDMS（原件唯一存储），向量化丢后台异步执行。

    folder_id 为空时默认写入 SeedDMS 根文件夹（id=1）。
    文件不再落伏羲本地 data/uploads/，原件只存 SeedDMS。
    写入 DMS 后立即返回 dms_doc_id，向量化入后台线程，不阻塞上传请求。
    """
    from pathlib import Path as _Path
    safe_name = _Path(file.filename or "").name or "upload"
    from config import MAX_UPLOAD_SIZE_MB
    limit_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    # 读取上传字节流（限制大小）
    content = bytearray()
    while chunk := await file.read(8 * 1024 * 1024):
        content.extend(chunk)
        if len(content) > limit_bytes:
            raise BizError(ErrorCode.UPLOAD_TOO_LARGE, f"文件过大，上限 {MAX_UPLOAD_SIZE_MB}MB")
    if len(content) == 0:
        raise BizError(ErrorCode.EMPTY_FILE)
    content_bytes = bytes(content)

    # 1) 写入 SeedDMS（原件唯一存储）——这一步同步完成，快速返回
    from src.dms.writer import upload_document, SeedDMSWriteError
    target_folder = folder_id or 1  # 默认根文件夹
    try:
        dms_doc_id = upload_document(
            folder_id=target_folder,
            filename=safe_name,
            content=content_bytes,
        )
    except SeedDMSWriteError as e:
        raise BizError(ErrorCode.DMS_WRITE_FAILED, str(e))
    except Exception as e:
        logger.error(f"写入 SeedDMS 失败: {e}")
        raise BizError(ErrorCode.DMS_WRITE_FAILED, f"写入 SeedDMS 失败: {e}")

    # 2) 向量化丢后台异步执行（不阻塞上传）
    task_id = _spawn_vectorize(dms_doc_id, safe_name)

    return {
        "status": "ok",
        "data": {
            "dms_doc_id": dms_doc_id,
            "task_id": task_id,
            "filename": safe_name,
            "message": "已存入 SeedDMS，正在后台向量化",
        },
    }


# 后台向量化任务登记表：dms_doc_id -> {task_id, filename, created_at}
# 供前端按 dms_doc_id 反查入库引擎 task_id，从而轮询实时进度。
_vectorize_jobs: dict[int, dict] = {}


def _spawn_vectorize(dms_doc_id: int, filename: str) -> str | None:
    """后台线程：从 SeedDMS 拉取文档并向量化入库（复用 import_service 闭环）。

    返回 task_id（入库引擎 task_id，用于轮询实时进度），但 task_id 是在 enqueue
    时才生成的，故这里先登记占位，真正 task_id 由 on_task 回调回填。
    """
    import threading
    import time
    from src.dms import import_service

    _vectorize_jobs[dms_doc_id] = {
        "task_id": None,
        "filename": filename,
        "created_at": time.time(),
    }

    def _on_task(task_id, doc_id):
        job = _vectorize_jobs.get(doc_id)
        if job:
            job["task_id"] = task_id

    def _work():
        try:
            result = import_service.import_documents(
                doc_ids=[dms_doc_id], folder_ids=[], on_task=_on_task
            )
            job = _vectorize_jobs.get(dms_doc_id)
            if result.get("failed"):
                logger.error(f"后台向量化失败 doc_id={dms_doc_id}: {result['failed']}")
                if job:
                    job["status"] = "failed"
                    job["error"] = str(result["failed"])
            else:
                logger.info(f"后台向量化完成 doc_id={dms_doc_id} ({filename})")
                if job:
                    job["status"] = "done"
                    from src.dms.sync_state import get_import_record
                    rec = get_import_record(dms_doc_id)
                    job["file_id"] = rec.get("file_id") if rec else None
        except Exception as e:
            logger.error(f"后台向量化异常 doc_id={dms_doc_id} ({filename}): {e}")
            job = _vectorize_jobs.get(dms_doc_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(e)

    t = threading.Thread(target=_work, name=f"dms-vectorize-{dms_doc_id}", daemon=True)
    t.start()
    return None  # 真实 task_id 稍后由 on_task 回填


@router.get("/api/documents/upload/{task_id}/progress")
def api_upload_progress(task_id: str, user=Depends(get_current_user)):
    from src.pipeline.engine import get_status
    status = get_status(task_id)
    if not status:
        raise BizError(ErrorCode.TASK_NOT_FOUND)
    return {"status": "ok", "data": status}


@router.get("/api/documents/upload/{dms_doc_id}/vectorize-status")
def api_vectorize_status(dms_doc_id: int, user=Depends(get_current_user)):
    """按 SeedDMS doc_id 反查后台向量化任务状态。

    返回 {task_id, job_status, file_id, error}：
      - task_id：入库引擎 task_id（用于轮询实时进度 /api/documents/upload/{task_id}/progress）
      - job_status：pending（等待）/ running（进行中）/ done（完成）/ failed（失败）
    """
    from src.pipeline.engine import get_status
    job = _vectorize_jobs.get(dms_doc_id)
    if job is None:
        raise HTTPException(404, "未找到该文档的向量化任务（可能已过期或服务重启）")

    task_id = job.get("task_id")
    # 推断 job 状态：优先用 job 显式标记，否则看 engine task 状态
    job_status = job.get("status")
    if job_status is None and task_id:
        st = get_status(task_id)
        if st:
            if st.get("status") == "done":
                job_status = "done"
            elif st.get("status") == "failed":
                job_status = "failed"
            else:
                job_status = "running"
        else:
            job_status = "running"
    elif job_status is None:
        job_status = "pending"

    return {
        "status": "ok",
        "data": {
            "dms_doc_id": dms_doc_id,
            "task_id": task_id,
            "job_status": job_status,
            "file_id": job.get("file_id"),
            "error": job.get("error"),
        },
    }


@router.get("/api/documents")
def api_list_files(category: str = None, model: str = None, material: str = None, date: str = None, folder: str = None, user=Depends(get_current_user)):
    files = list_files_with_entities(category=category, model=model, material=material, date=date, folder=folder)
    return {"status": "ok", "data": files}


@router.get("/api/folders")
def api_list_folders(user=Depends(get_current_user)):
    return {"status": "ok", "data": list_folders()}


@router.put("/api/documents/{file_id}/folder")
def api_move_file(file_id: int, req: FolderReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    update_file_folder(file_id, req.folder)
    return {"status": "ok"}


@router.get("/api/documents/{file_id}")
def api_get_file(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    chunks = get_chunks_by_file(file_id)
    return {"status": "ok", "data": {"file": f, "chunks": chunks}}


@router.get("/api/documents/{file_id}/backlinks")
def api_file_backlinks(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    data = get_file_backlinks(file_id)
    return {"status": "ok", "data": data}


@router.get("/api/chunks/{chunk_id}/refs")
def api_chunk_refs(chunk_id: int, user=Depends(get_current_user)):
    """反查：某 chunk 被哪些对话引用过（文档详情「被引用」角标的数据源）。

    扫描 conversation_messages.sources 找含该 chunk_id 的 assistant 消息，
    返回引用它的对话 + 用户提问（供前端展示「这一段被谁问过/引用过」）。
    """
    from src.storage.db import get_chunk_references
    refs = get_chunk_references(chunk_id)
    return {"status": "ok", "data": refs}


@router.get("/api/documents/{file_id}/chunk-refs")
def api_file_chunk_refs(file_id: int, user=Depends(get_current_user)):
    """批量统计某文档所有 chunk 的被引用次数 {chunk_id: count}。

    供文档详情给每个 chunk 前置显示「被引用 N 次」角标，避免 N+1 请求。
    """
    from src.storage.db import get_chunk_ref_counts
    chunks = get_chunks_by_file(file_id)
    cids = {c["id"] for c in chunks}
    counts = get_chunk_ref_counts(cids)
    return {"status": "ok", "data": counts}


@router.get("/api/documents/{file_id}/markdown")
def api_file_markdown(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    chunks = get_chunks_by_file(file_id)
    from src.pipeline.markdown_render import chunks_to_markdown
    md = chunks_to_markdown(f["name"], chunks)
    return {"status": "ok", "data": {"markdown": md, "file_name": f["name"]}}


@router.get("/api/documents/{file_id}/images")
def api_file_images(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    imgs = list_images(file_id)
    return {"status": "ok", "data": imgs}


@router.delete("/api/documents/{file_id}")
def api_delete_file(file_id: int, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    delete_file(file_id)
    return {"status": "ok"}


@router.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str, user=Depends(require_admin)):
    """取消入库任务（设置 cancelled 标记，引擎循环内检查）"""
    from src.storage.tasks import cancel_task
    ok = cancel_task(task_id)
    if not ok:
        raise HTTPException(404, "任务不存在或已完成")
    return {"status": "ok", "data": {"task_id": task_id, "cancelled": True}}


@router.put("/api/documents/{file_id}/category")
def api_set_category(file_id: int, req: CategoryReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    update_file_category(file_id, req.category)
    return {"status": "ok"}


@router.put("/api/documents/{file_id}/tags")
def api_set_tags(file_id: int, req: TagsReq, user=Depends(require_admin)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    update_file_tags(file_id, req.tags)
    return {"status": "ok"}


@router.get("/api/health")
def api_health():
    """健康检查：返回服务状态 + 关键依赖健康度（DB/Chroma/LLM/磁盘）。

    用于内网部署后的运维探测，整体 status 仅当核心依赖（DB）不可用时才降为 degraded。
    """
    import os
    from config import DB_PATH
    checks = {}
    healthy = True

    # 1. SQLite 可达
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        healthy = False

    # 2. Chroma 可达（可选，取决于 RAG_CHROMA）
    try:
        from config import RAG_CHROMA
        if RAG_CHROMA == "1":
            from src.storage.chroma_store import _use_chroma
            checks["chroma"] = "ok" if _use_chroma() else "disabled"
        else:
            checks["chroma"] = "disabled"
    except Exception as e:
        checks["chroma"] = f"error: {e}"

    # 3. LLM key 配置（软检查，不调用）
    from src.llm import _has_any_key
    checks["llm_configured"] = _has_any_key()

    # 4. 磁盘剩余（工作目录所在盘）
    try:
        usage = __import__("shutil").disk_usage(os.getcwd())
        checks["disk_free_gb"] = round(usage.free / 1024 / 1024 / 1024, 2)
    except Exception as e:
        checks["disk_free_gb"] = f"error: {e}"

    # 5. LLM 调用审计（降级率体温计，可观测性）
    try:
        from src.llm_audit import get_stats, degradation_rate
        checks["llm_degradation_rate"] = degradation_rate()
        stats = get_stats()
        checks["llm_calls"] = stats["calls"]
        checks["llm_total_tokens"] = {"in": stats["total_tokens_in"], "out": stats["total_tokens_out"]}
    except Exception as e:
        checks["llm_audit"] = f"error: {e}"

    # 6. 最近一次检索耗时 profile（可观测性）
    try:
        from src.retrieval.search import get_last_profile
        p = get_last_profile()
        if p:
            checks["last_search_profile_ms"] = p
    except Exception:
        pass

    # 7. 最近一次对话链路耗时 profile（可观测性，与检索 profile 对齐）
    try:
        from src.chat.orchestrator import get_last_chat_profile
        cp = get_last_chat_profile()
        if cp:
            checks["last_chat_profile_ms"] = cp
    except Exception:
        pass

    return {
        "status": "ok" if healthy else "degraded",
        "service": "rag-framework",
        "checks": checks,
    }
