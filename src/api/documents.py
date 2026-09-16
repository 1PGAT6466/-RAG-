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
    P22: 同名/同内容文件去重（sha256 检测）。
    """
    from pathlib import Path as _Path
    import hashlib
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

    # P22: 计算 sha256 并检查去重
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    from src.storage.files import get_file_by_hash
    existing = get_file_by_hash(content_hash)
    if existing:
        logger.info(f"P22 去重: 文件 '{safe_name}' 与已有文件 id={existing['id']} '{existing['name']}' 内容相同，跳过")
        from src.storage.audit import log_action
        log_action(user_id=user.get("id"), username=user.get("username"),
                   action="upload_dedup", target_type="file", target_id=existing["id"],
                   detail=f"filename={safe_name}, existing={existing['name']}")
        return {
            "status": "ok",
            "data": {
                "dms_doc_id": None,
                "task_id": None,
                "filename": safe_name,
                "existing_file_id": existing['id'],
                "existing_file_name": existing['name'],
                "message": f"内容与已有文件 '{existing['name']}'(id={existing['id']}) 完全相同，跳过重复入库",
                "dedup": True,
            },
        }

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
    task_id = _spawn_vectorize(dms_doc_id, safe_name, content_hash=content_hash)

    return {
        "status": "ok",
        "data": {
            "dms_doc_id": dms_doc_id,
            "task_id": task_id,
            "filename": safe_name,
            "message": "已存入 SeedDMS，正在后台向量化",
            "dedup": False,
        },
    }


# 后台向量化任务登记表：dms_doc_id -> {task_id, filename, created_at}
# 供前端按 dms_doc_id 反查入库引擎 task_id，从而轮询实时进度。
_vectorize_jobs: dict[int, dict] = {}


def _spawn_vectorize(dms_doc_id: int, filename: str, content_hash: str = None) -> str | None:
    """后台线程：从 SeedDMS 拉取文档并向量化入库（复用 import_service 闭环）。

    返回 task_id（入库引擎 task_id，用于轮询实时进度），但 task_id 是在 enqueue
    时才生成的，故这里先登记占位，真正 task_id 由 on_task 回调回填。
    """
    import threading
    import time
    from src.dms import import_service

    # W11: 清理过期条目，防止内存泄漏
    now = time.time()
    stale_keys = [k for k, v in _vectorize_jobs.items() if now - v.get("created_at", 0) > 3600]
    for k in stale_keys:
        _vectorize_jobs.pop(k, None)
    if len(_vectorize_jobs) > 500:
        oldest = sorted(_vectorize_jobs.items(), key=lambda x: x[1].get("created_at", 0))[:len(_vectorize_jobs) - 500]
        for k, _ in oldest:
            _vectorize_jobs.pop(k, None)

    _vectorize_jobs[dms_doc_id] = {
        "task_id": None,
        "filename": filename,
        "content_hash": content_hash,
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
                    # P22: 回填 content_hash
                    if job.get("content_hash") and job.get("file_id"):
                        try:
                            from src.storage.files import update_file_hash
                            update_file_hash(job["file_id"], job["content_hash"])
                        except Exception as e:
                            logger.warning(f"P22 回填 content_hash 失败: {e}")
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
    """获取文档列表（支持分类/型号/材料/日期/文件夹筛选）

    返回文件列表，每个文件附带关联的型号和材料实体。
    """
    files = list_files_with_entities(category=category, model=model, material=material, date=date, folder=folder)
    return {"status": "ok", "data": files}


@router.get("/api/folders")
def api_list_folders(user=Depends(get_current_user)):
    """获取文件夹树（含文件计数）"""
    return {"status": "ok", "data": list_folders()}


@router.put("/api/documents/{file_id}/folder")
def api_move_file(file_id: int, req: FolderReq, user=Depends(require_admin)):
    """移动文件到指定文件夹"""
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    update_file_folder(file_id, req.folder)
    return {"status": "ok"}


# === P23: 回收站（必须在 {file_id} 路由之前，否则被通配符吃掉） ===

@router.get("/api/documents/recycle-bin")
def api_recycle_bin(user=Depends(require_admin)):
    """P23: 列出回收站中的文件"""
    from src.storage.files import list_deleted_files
    files = list_deleted_files()
    return {"status": "ok", "data": files}


@router.post("/api/documents/{file_id}/restore")
def api_restore_file(file_id: int, user=Depends(require_admin)):
    """P23: 从回收站恢复文件"""
    from src.storage.files import restore_file
    ok = restore_file(file_id)
    if not ok:
        raise BizError(ErrorCode.FILE_NOT_FOUND, detail="文件不在回收站中")
    from src.storage.audit import log_action
    log_action(user_id=user.get("id"), username=user.get("username"),
               action="restore", target_type="file", target_id=file_id)
    return {"status": "ok"}


@router.delete("/api/documents/{file_id}/permanent")
def api_permanent_delete(file_id: int, user=Depends(require_admin)):
    """P23: 永久删除（不可恢复）"""
    from src.storage.files import permanent_delete_file
    from src.storage.db import _get_conn
    f = dict(_get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone() or {})
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    permanent_delete_file(file_id)
    from src.storage.audit import log_action
    log_action(user_id=user.get("id"), username=user.get("username"),
               action="permanent_delete", target_type="file", target_id=file_id,
               detail=f"name={f['name']}")
    return {"status": "ok"}


@router.get("/api/documents/{file_id}")
def api_get_file(file_id: int, user=Depends(get_current_user)):
    """获取文件详情（含所有 chunk）"""
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    chunks = get_chunks_by_file(file_id)
    return {"status": "ok", "data": {"file": f, "chunks": chunks}}


@router.get("/api/documents/{file_id}/backlinks")
def api_file_backlinks(file_id: int, user=Depends(get_current_user)):
    """获取文件的双向链接（Obsidian 式 backlinks）"""
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


def _serve_file(file_id: int, disposition: str):
    """内部：返回文件流。disposition='inline'=预览，'attachment'=下载。"""
    from fastapi.responses import FileResponse
    import os
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    fpath = f.get("path", "")
    if not fpath or not os.path.isfile(fpath):
        raise BizError(ErrorCode.FILE_NOT_FOUND, "原始文件不存在（可能已被清理）")
    ext = (f.get("ext") or "").lower()
    mime_map = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pdf": "application/pdf",
    }
    media_type = mime_map.get(ext, "application/octet-stream")
    from urllib.parse import quote
    # RFC 5987: 文件名中文编码
    ascii_name = f["name"].encode('ascii', 'ignore').decode() or 'file'
    utf8_name = quote(f["name"])
    cd = f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
    return FileResponse(
        fpath, media_type=media_type,
        headers={"Content-Disposition": cd},
    )


@router.get("/api/documents/{file_id}/raw")
def api_file_raw(file_id: int, user=Depends(get_current_user)):
    """预览：inline 返回，浏览器内展示不触发下载。"""
    return _serve_file(file_id, "inline")


@router.get("/api/documents/{file_id}/download")
def api_file_download(file_id: int, user=Depends(require_admin)):
    """下载：仅管理员可用，attachment 触发浏览器下载。"""
    return _serve_file(file_id, "attachment")


@router.get("/api/documents/{file_id}/images")
def api_file_images(file_id: int, user=Depends(get_current_user)):
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    imgs = list_images(file_id)
    return {"status": "ok", "data": imgs}


@router.delete("/api/documents/{file_id}")
def api_delete_file(file_id: int, user=Depends(require_admin)):
    """删除文件（软删除，移入回收站）"""
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    delete_file(file_id)
    from src.storage.audit import log_action
    log_action(user_id=user.get("id"), username=user.get("username"),
               action="delete", target_type="file", target_id=file_id,
               detail=f"name={f['name']}")
    return {"status": "ok"}


@router.get("/api/audit")
def api_audit_logs(limit: int = 100, action: str = None, user=Depends(require_admin)):
    """查询审计日志"""
    # S9: limit 参数上限约束
    limit = min(limit, 1000)
    from src.storage.audit import get_audit_logs
    logs = get_audit_logs(limit=limit, action=action)
    return {"status": "ok", "data": logs}


@router.get("/api/documents/{file_id}/permissions")
def api_get_permissions(file_id: int, user=Depends(require_admin)):
    """获取文件权限"""
    from src.storage.permissions import get_file_permissions
    perms = get_file_permissions(file_id)
    return {"status": "ok", "data": perms}


@router.post("/api/documents/{file_id}/permissions")
def api_set_permission(file_id: int, user_id: int = None, role: str = None,
                      permission: str = "read", user=Depends(require_admin)):
    """设置文件权限"""
    # S17: 参数枚举校验
    _VALID_ROLES = {"admin", "user", "viewer", None}
    _VALID_PERMISSIONS = {"read", "write", "admin"}
    if role not in _VALID_ROLES:
        raise HTTPException(400, f"role 必须是 admin/user/viewer 或留空，收到: {role}")
    if permission not in _VALID_PERMISSIONS:
        raise HTTPException(400, f"permission 必须是 read/write/admin，收到: {permission}")
    from src.storage.permissions import set_file_permission
    set_file_permission(file_id, user_id=user_id, role=role, permission=permission)
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
    """设置文件分类"""
    f = get_file(file_id)
    if not f:
        raise BizError(ErrorCode.FILE_NOT_FOUND)
    update_file_category(file_id, req.category)
    return {"status": "ok"}


@router.put("/api/documents/{file_id}/tags")
def api_set_tags(file_id: int, req: TagsReq, user=Depends(require_admin)):
    """设置文件标签"""
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
    from src.llm_client import _has_any_key
    checks["llm_configured"] = _has_any_key()

    return {
        "status": "ok" if healthy else "degraded",
        "service": "rag-framework",
        "checks": checks,
    }


@router.get("/api/metrics")
def api_metrics(user=Depends(require_admin)):
    """P25: 持续指标端点（JSON 格式，供巡检器/脚本消费）"""
    from src.metrics import get_metrics
    return get_metrics()
