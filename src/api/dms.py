"""
api/dms.py — SeedDMS 文档源接入路由

接口：
  GET   /api/dms/health        连接状态（SeedDMS 是否可达 + 登录态）
  GET   /api/dms/tree          文件夹树（递归）
  POST  /api/dms/import        手动勾选导入（doc_ids + folder_ids）
  GET   /api/dms/records       已导入映射列表（追溯）
  GET   /api/dms/config        连接配置（密码不回显明文）
  PUT   /api/dms/config        更新连接配置（写 .env）
  GET   /api/dms/check-updates 检查已导入文档有无新版本
  POST  /api/dms/replace-all   一键替换所有新版本
  GET   /api/dms/record        按 file_id 反查 SeedDMS 来源
  POST  /api/dms/reconnect     重置连接缓存
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.auth.deps import require_admin, get_current_user
from src.dms.seeddms_client import get_client, reset_client
from src.dms import import_service, sync_state

logger = logging.getLogger("rag.api.dms")
router = APIRouter()


class DmsImportReq(BaseModel):
    doc_ids: list[int] = Field(default_factory=list)
    folder_ids: list[int] = Field(default_factory=list)


class DmsConfigReq(BaseModel):
    url: str = Field(default="", max_length=256)
    user: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=128)


@router.get("/api/dms/health")
def api_dms_health(user=Depends(require_admin)):
    """连接状态：SeedDMS 可达性 + 登录态"""
    client = get_client()
    reachable = client.ping()
    logged_in = False
    if reachable:
        logged_in = client.login()
    return {
        "status": "ok",
        "data": {
            "reachable": reachable,
            "logged_in": logged_in,
            "url": client.base_url,
            "user": client.user,
        },
    }


@router.get("/api/dms/folders")
def api_dms_folders(user=Depends(require_admin)):
    """纯文件夹列表（供浏览器上传时选择目标文件夹，扁平 list）"""
    from src.dms.writer import list_folders, SeedDMSWriteError
    try:
        folders = list_folders()
        return {"status": "ok", "data": folders}
    except SeedDMSWriteError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.error(f"拉取 SeedDMS 文件夹列表失败: {e}")
        raise HTTPException(500, f"读取 SeedDMS 文件夹失败: {e}")


@router.get("/api/dms/tree")
def api_dms_tree(user=Depends(require_admin)):
    """文件夹树"""
    client = get_client()
    try:
        if not client.login():
            raise HTTPException(502, "SeedDMS 登录失败")
        tree = client.get_folder_tree()
        if not tree.get("success"):
            raise HTTPException(502, tree.get("message", "拉取文件夹树失败"))
        return {"status": "ok", "data": tree["data"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"拉取 DMS 文件夹树失败: {e}")
        raise HTTPException(502, f"SeedDMS 连接失败: {e}")


@router.get("/api/dms/documents")
def api_dms_documents(
    page: int = 1,
    page_size: int = 50,
    status: str = "all",
    user=Depends(require_admin),
):
    """DMS 全部文档分页列表（每页默认 50），支持已导入/未导入筛选。

    status: all | imported | unimported
    返回 {total, page, page_size, items:[{id,name,type,version,folder_path,imported,file_id}]}
    """
    from src.storage.db import _get_conn

    client = get_client()
    try:
        if not client.login():
            raise HTTPException(502, "SeedDMS 登录失败")

        docs = client.get_documents_flat()

        # 已导入映射（dms_doc_id -> file_id）
        conn = _get_conn()
        rows = conn.execute("SELECT dms_doc_id, file_id FROM dms_imports").fetchall()
        imported_map = {r["dms_doc_id"]: r["file_id"] for r in rows}

        for d in docs:
            fid = imported_map.get(d["id"])
            d["imported"] = fid is not None
            d["file_id"] = fid

        # 筛选
        if status == "imported":
            docs = [d for d in docs if d["imported"]]
        elif status == "unimported":
            docs = [d for d in docs if not d["imported"]]

        total = len(docs)
        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        start = (page - 1) * page_size
        items = docs[start : start + page_size]

        return {
            "status": "ok",
            "data": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": items,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"拉取 DMS 文档列表失败: {e}")
        raise HTTPException(502, f"SeedDMS 文档列表获取失败: {e}")


@router.post("/api/dms/import")
def api_dms_import(req: DmsImportReq, user=Depends(require_admin)):
    """手动勾选导入"""
    if not req.doc_ids and not req.folder_ids:
        raise HTTPException(400, "请勾选要导入的文档或文件夹")
    try:
        result = import_service.import_documents(req.doc_ids, req.folder_ids)
        return {"status": "ok", "data": result}
    except import_service.DMSNotConfiguredError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.error(f"DMS 导入失败: {e}")
        raise HTTPException(500, f"导入失败: {e}")


@router.get("/api/dms/records")
def api_dms_records(user=Depends(require_admin)):
    """已导入映射列表（追溯）"""
    from src.storage.db import _get_conn
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM dms_imports ORDER BY imported_at DESC"
    ).fetchall()
    return {"status": "ok", "data": [dict(r) for r in rows]}


@router.post("/api/dms/reconnect")
def api_dms_reconnect(user=Depends(require_admin)):
    """重置连接缓存（配置变更后重新登录）"""
    reset_client()
    return {"status": "ok"}


# === 连接配置（密码不回显明文）===

@router.get("/api/dms/config")
def api_dms_get_config(user=Depends(require_admin)):
    """读取 SeedDMS 连接配置（密码不回显明文，只返回是否已配置）"""
    from config import SEEDDMS_URL, SEEDDMS_USER, SEEDDMS_PASS
    return {
        "status": "ok",
        "data": {
            "url": SEEDDMS_URL,
            "user": SEEDDMS_USER,
            "password_set": bool(SEEDDMS_PASS),
        },
    }


@router.put("/api/dms/config")
def api_dms_set_config(req: DmsConfigReq, user=Depends(require_admin)):
    """更新 SeedDMS 连接配置，写回 .env。

    密码字段：若 req.password 为空，则保留原密码不变（不回显明文的场景下，
    用户不填即不改）；若非空，则更新。
    """
    from pathlib import Path
    from config import BASE_DIR
    ENV_PATH = BASE_DIR / ".env"

    changes = {}
    if req.url:
        changes["SEEDDMS_URL"] = req.url.strip()
    if req.user:
        changes["SEEDDMS_USER"] = req.user.strip()
    if req.password:
        changes["SEEDDMS_PASS"] = req.password

    if not changes:
        raise HTTPException(400, "没有需要更新的配置项")

    # 写回 .env（保留原有内容，仅替换/追加 SEEDDMS 相关 key）
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    existing = {}
    for i, ln in enumerate(lines):
        ln2 = ln.strip()
        if "=" in ln2 and not ln2.startswith("#"):
            k = ln2.split("=", 1)[0].strip()
            if k in changes:
                existing[k] = i
    for k, v in changes.items():
        if k in existing:
            lines[existing[k]] = f"{k}={v}"
        else:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 重置连接缓存，下次调用用新配置重新登录
    reset_client()
    logger.info(f"SeedDMS 连接配置已更新: {list(changes.keys())}")
    return {"status": "ok", "updated": list(changes.keys())}


# === 批量替换：检查已导入文档有无新版本 ===

@router.get("/api/dms/check-updates")
def api_dms_check_updates(user=Depends(require_admin)):
    """扫描所有已导入文档，对比 SeedDMS 当前内容 hash，返回有新版本的文档列表"""
    try:
        updates = import_service.check_updates()
        return {"status": "ok", "data": updates}
    except import_service.DMSNotConfiguredError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.error(f"DMS 检查更新失败: {e}")
        raise HTTPException(500, f"检查更新失败: {e}")


@router.post("/api/dms/replace-all")
def api_dms_replace_all(user=Depends(require_admin)):
    """一键替换所有有新版本的文档（重新导入）"""
    try:
        result = import_service.replace_all()
        return {"status": "ok", "data": result}
    except import_service.DMSNotConfiguredError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.error(f"DMS 批量替换失败: {e}")
        raise HTTPException(500, f"批量替换失败: {e}")


# === 反查：已知 file_id 查 SeedDMS 来源 ===

# === 下载：从 SeedDMS 获取文档内容 ===

@router.get("/api/dms/download/{doc_id}")
def api_dms_download(doc_id: int, user=Depends(get_current_user)):
    """从 SeedDMS 下载文档内容（二进制流）"""
    from fastapi.responses import Response
    client = get_client()
    try:
        if not client.login():
            raise HTTPException(502, "SeedDMS 登录失败")
        content, filename, mime = client.download_document(doc_id)
        return Response(
            content=content,
            media_type=mime or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(content)),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DMS 文档下载失败 doc_id={doc_id}: {e}")
        raise HTTPException(502, f"下载失败: {e}")


@router.get("/api/dms/record")
def api_dms_record_by_file(file_id: int, user=Depends(get_current_user)):
    """按伏羲 file_id 反查 SeedDMS 来源记录（供文档详情页展示）"""
    from src.dms.sync_state import get_import_record_by_file
    record = get_import_record_by_file(file_id)
    return {"status": "ok", "data": record}
