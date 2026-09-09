"""
import_service.py — SeedDMS 导入编排 + 自动替换

核心流程：
  1. 遍历选中项（文档 + 文件夹递归展开）
  2. 对每个文档：
     - 未导入 → 下载 → enqueue 入库 → 记映射(imported)
     - 已导入但 content_hash 变了（新版本）→ 下载 → 替换旧数据 → 更新映射(replaced)
     - 已导入且 hash 未变 → 跳过(幂等)

替换顺序严格遵循「先删数据库 → 再删 Chroma 向量 → 重新入库」。
"""
import logging
import os
import tempfile
import uuid
from pathlib import Path

from config import UPLOAD_DIR

from .seeddms_client import get_client
from . import sync_state

logger = logging.getLogger("rag.dms.import_service")


class DMSNotConfiguredError(Exception):
    pass


def _ensure_login(client) -> None:
    if not client.login():
        raise DMSNotConfiguredError("SeedDMS 登录失败，请检查连接配置")


def _collect_items(client, doc_ids: list[int], folder_ids: list[int]) -> list[dict]:
    """把选中的文档 + 文件夹递归展开成「待导入文档」列表。

    返回 [{doc_id, folder_path, name}]，folder_path 为 SeedDMS 里的路径快照。
    """
    items = []

    def _walk_folder(folder_id: int, path_prefix: str):
        resp = client.get_folder_children(folder_id)
        if not resp.get("success"):
            logger.warning(f"文件夹 {folder_id} 读取失败: {resp.get('message')}")
            return
        for item in resp.get("data", []):
            name = item.get("name", "")
            item_type = item.get("type", "folder")
            if item_type == "folder":
                # 记录文件夹路径快照（用于伏羲虚拟目录归类）
                sub_path = f"{path_prefix}/{name}" if path_prefix else f"/{name}"
                _walk_folder(item["id"], sub_path)
            else:
                items.append({
                    "doc_id": item["id"],
                    "folder_path": path_prefix or "/",
                    "name": name,
                    "version": item.get("version", 0),
                })

    for folder_id in (folder_ids or []):
        _walk_folder(folder_id, "")

    # 单独勾选的文档（不在文件夹遍历内）
    for doc_id in (doc_ids or []):
        if not any(d["doc_id"] == doc_id for d in items):
            try:
                meta = client.get_document_meta(doc_id)
                doc_data = meta.get("data", {}) if meta.get("success") else {}
                items.append({
                    "doc_id": doc_id,
                    "folder_path": doc_data.get("folder", "/"),
                    "name": doc_data.get("name", f"document_{doc_id}"),
                    "version": doc_data.get("version", 0),
                })
            except Exception as e:
                logger.warning(f"获取文档 {doc_id} 元数据失败: {e}")

    return items


def import_documents(doc_ids: list[int], folder_ids: list[int], on_task=None) -> dict:
    """导入选中项（文档 + 文件夹），返回结果摘要。

    on_task: 可选回调 on_task(task_id, doc_id)，在 enqueue 后立即调用，用于外部
            获取入库引擎的 task_id（以轮询实时进度）。

    返回 {imported: int, replaced: int, skipped: int, failed: [{doc_id, name, error}]}
    """
    client = get_client()
    _ensure_login(client)

    items = _collect_items(client, doc_ids, folder_ids)

    imported, replaced, skipped = 0, 0, 0
    failed = []

    for item in items:
        doc_id = item["doc_id"]
        name = item["name"]
        try:
            # 下载文档内容
            content, filename, _mime = client.download_document(doc_id)
            if not content:
                raise ValueError("下载内容为空")

            fname = filename or name or f"document_{doc_id}"
            content_hash = sync_state.content_hash_of(content)

            # 幂等判断：查映射
            record = sync_state.get_import_record(doc_id)
            old_file_id = record.get("file_id") if record else None

            # 判断伏羲里旧 file 是否真实存在（防映射指向已删文件的脏数据）
            old_file_exists = False
            if old_file_id:
                from src.storage.db import get_file
                old_file_exists = get_file(old_file_id) is not None

            # 内容未变 且 旧文件仍在 → 跳过（幂等）
            if record and record.get("content_hash") == content_hash and old_file_exists:
                skipped += 1
                logger.info(f"DMS 文档 {doc_id} 内容未变，跳过")
                continue

            # 到这里：要么未导入，要么内容变了，要么映射指向的文件已丢失 → 走（重新）导入
            # is_replace：旧文件真实存在（说明是内容变化触发的替换，而非重新导入）
            is_replace = bool(old_file_id and old_file_exists)

            # 1) 写临时文件 → enqueue 入库（伏羲完整 pipeline）
            tmp = Path(tempfile.gettempdir()) / f"_dms_{uuid.uuid4().hex[:8]}_{fname}"
            tmp.write_bytes(content)
            try:
                from src.pipeline.engine import enqueue
                task_id = enqueue(str(tmp), fname)
                # 回调：把 task_id 暴露给外部（用于轮询实时进度）
                if on_task:
                    try:
                        on_task(task_id, doc_id)
                    except Exception as e:
                        logger.warning(f"on_task 回调失败（已忽略）: {e}")
                # 同步等待入库完成，拿到 file_id（入库 engine 后台线程，这里轮询）
                file_id = _wait_for_file(task_id, timeout=120)
                if file_id is None:
                    raise RuntimeError("入库超时或失败")
            finally:
                # 临时文件由 engine 清理（_tmp_ 前缀），但这里确保兜底清理
                try:
                    if tmp.name.startswith("_dms_") and tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass

            # 2) 若为替换，清理旧数据（先库后缓存）；注意要在新数据入库成功后删旧，
            #    顺序：先完成新入库拿到新 file_id，再删旧的（避免窗口期无数据）
            if is_replace and old_file_id != file_id:
                try:
                    sync_state.replace_file_cleanup(old_file_id)
                except Exception as e:
                    logger.warning(f"旧文件 {old_file_id} 清理失败（已忽略，映射仍保留）: {e}")

            # 3) 更新映射
            status = "replaced" if is_replace else "imported"
            sync_state.upsert_import_record(
                dms_doc_id=doc_id,
                file_id=file_id,
                dms_version=item.get("version", 0),
                dms_folder_path=item["folder_path"],
                dms_name=fname,
                content_hash=content_hash,
                status=status,
            )

            if is_replace:
                replaced += 1
            else:
                imported += 1
            logger.info(f"DMS 文档 {doc_id} ({fname}) 导入完成 (file_id={file_id}, {status})")

        except Exception as e:
            logger.error(f"DMS 文档 {doc_id} ({name}) 导入失败: {e}")
            failed.append({"doc_id": doc_id, "name": name, "error": str(e)})

    return {
        "imported": imported,
        "replaced": replaced,
        "skipped": skipped,
        "failed": failed,
        "total": len(items),
    }


def _wait_for_file(task_id: str, timeout: int = 120) -> int | None:
    """轮询 engine 任务直到完成，返回 file_id（超时/失败返回 None）

    智能超时：优先按「任务是否在推进」判断，而非固定墙钟时间一刀切。
    大文档（本地 CPU 向量化/流式 PDF/OCR）单文档就可能 >120s，固定 timeout 会
    误判为「入库超时/失败」（历史坑）。若任务 stage/progress 持续在变，说明仍在
    正常推进，继续等待；只有连续「无进展」超过 timeout 才判定超时。
    """
    import time
    from src.pipeline.engine import get_status

    deadline = time.time() + timeout
    last_progress = None
    last_change = time.time()
    while True:
        st = get_status(task_id)
        if not st:
            return None
        status = st.get("status")
        if status == "done":
            return st.get("file_id")
        if status == "failed":
            logger.warning(f"入库任务 {task_id} 失败: {st.get('error')}")
            return None

        # 推进检测：progress/stage 变即视为在推进，重置「无进展」计时
        progress_key = (st.get("stage"), st.get("progress"), st.get("chunks"))
        if progress_key != last_progress:
            last_progress = progress_key
            last_change = time.time()
        elif time.time() - last_change > timeout:
            # 连续 timeout 秒无任何进展 → 判定卡死超时
            logger.warning(f"入库任务 {task_id} 连续 {timeout}s 无进展，判定超时")
            return None

        time.sleep(0.5)



def check_updates() -> list[dict]:
    """扫描所有已导入文档，对比 SeedDMS 当前内容 hash，返回有新版本的文档列表。

    返回 [{dms_doc_id, dms_name, current_version, stored_version, file_id}]
    """
    from src.storage.db import _get_conn
    client = get_client()
    _ensure_login(client)

    conn = _get_conn()
    records = conn.execute("SELECT * FROM dms_imports").fetchall()

    updates = []
    for rec in records:
        doc_id = rec["dms_doc_id"]
        try:
            content, _filename, _mime = client.download_document(doc_id)
            new_hash = sync_state.content_hash_of(content)
            if new_hash != rec["content_hash"]:
                updates.append({
                    "dms_doc_id": doc_id,
                    "dms_name": rec["dms_name"],
                    "stored_version": rec["dms_version"],
                    "file_id": rec["file_id"],
                    "stored_hash": rec["content_hash"],
                    "new_hash": new_hash,
                })
        except Exception as e:
            logger.warning(f"检查文档 {doc_id} 更新失败（已忽略）: {e}")

    return updates


def replace_all() -> dict:
    """一键替换所有有新版本的文档（复用 check_updates + import_documents）"""
    updates = check_updates()
    doc_ids = [u["dms_doc_id"] for u in updates]
    if not doc_ids:
        return {"updated": 0, "checked": len(updates), "details": [], "message": "没有需要更新的文档"}

    result = import_documents(doc_ids=doc_ids, folder_ids=[])
    return {
        "updated": result["replaced"] + result["imported"],
        "checked": len(updates),
        "details": updates,
        "import_result": result,
    }

