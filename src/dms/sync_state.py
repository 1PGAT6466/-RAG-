"""
sync_state.py — SeedDMS 导入映射表读写 + 替换逻辑

dms_imports 表是「可追溯」的基石：记录伏羲 file_id ↔ SeedDMS document_id/version 的映射，
用于：
  1. 幂等：重复导入时跳过「已导入且版本未变」的文档
  2. 追溯：从伏羲文件反查「来自 SeedDMS 哪个文档、哪个版本」
  3. 替换：识别「哪个 file_id 对应哪个 dms_doc_id」，新版本来了直接替换旧伏羲文件
"""
import logging
import hashlib

from src.storage.db import _get_conn

logger = logging.getLogger("rag.dms.sync_state")


def content_hash_of(data: bytes) -> str:
    """文档内容哈希（判断版本是否真正变化）"""
    return hashlib.sha256(data).hexdigest()


def get_import_record(dms_doc_id: int) -> dict | None:
    """查询某个 SeedDMS 文档的导入记录"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM dms_imports WHERE dms_doc_id=?", (dms_doc_id,)
    ).fetchone()
    return dict(row) if row else None


def get_import_record_by_file(file_id: int) -> dict | None:
    """按伏羲 file_id 反查导入记录"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM dms_imports WHERE file_id=?", (file_id,)
    ).fetchone()
    return dict(row) if row else None


def upsert_import_record(dms_doc_id: int, file_id: int, dms_version: int,
                         dms_folder_path: str, dms_name: str, content_hash: str,
                         status: str = "imported") -> None:
    """写入/更新导入映射（按 dms_doc_id 唯一）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO dms_imports (file_id, dms_doc_id, dms_version, dms_folder_path,
                                 dms_name, content_hash, status, imported_at)
        VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))
        ON CONFLICT(dms_doc_id) DO UPDATE SET
            file_id=excluded.file_id,
            dms_version=excluded.dms_version,
            dms_folder_path=excluded.dms_folder_path,
            dms_name=excluded.dms_name,
            content_hash=excluded.content_hash,
            status=excluded.status,
            imported_at=datetime('now','localtime')
        """,
        (file_id, dms_doc_id, dms_version, dms_folder_path, dms_name, content_hash, status),
    )
    conn.commit()


def remove_import_record(dms_doc_id: int) -> None:
    """删除导入映射（文档从 DMS 移除后清理）"""
    conn = _get_conn()
    conn.execute("DELETE FROM dms_imports WHERE dms_doc_id=?", (dms_doc_id,))
    conn.commit()


def replace_file_cleanup(file_id: int) -> None:
    """替换前的旧数据清理：先删数据库（SQLite 全链路），再删 Chroma 向量缓存。

    顺序严格遵循「先数据库后缓存」：
      1. SQLite：files（CASCADE 级联删 chunks/entity_chunks/entity_files/images）、
         FTS5（chunks_fts/chunks_fts_tri）、links（无外键，显式删）
      2. ChromaDB：chunk 向量（缓存层）
      3. 磁盘：图片目录

    直接复用 src.storage.files.delete_file，它已经按此顺序实现了完整清理。

    注意：此函数只负责删「伏羲旧文件数据」，不碰 dms_imports 映射——映射的
    更新（新 file_id / 新 hash / status=replaced）由 import_service 在删旧后
    重新入库完成后统一 upsert，避免出现「映射指向空 file_id」的窗口。
    """
    try:
        from src.storage.files import delete_file
        delete_file(file_id)
    except Exception as e:
        logger.error(f"替换旧文件 {file_id} 清理失败: {e}")
        raise
