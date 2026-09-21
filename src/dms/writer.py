"""
writer.py — 直接写入 SeedDMS 数据库 + 文件系统（浏览器上传 → DMS 唯一原件）

背景：
  SeedDMS 6.0.41 的 REST API 只读（login/download/search/tree），不支持创建文档。
  为了让「浏览器上传的文件」原件落在 SeedDMS（而非伏羲本地 data/uploads/），
  本模块直接操作 SeedDMS 的 SQLite 数据库 + 内容目录，模拟 SeedDMS 官方
  `Folder::addDocument()` / `Document::addContent()` 的写入逻辑。

依据（SeedDMS 6.0.41 源码，已核对）：
  - 表：tblDocuments（文档元数据）
        tblDocumentContent（版本内容 + md5 checksum）
        tblDocumentStatus（documentID + version）
        tblDocumentStatusLog（状态流转日志，初始 S_RELEASED）
  - 文件：contentDir/<contentOffsetDir>/<docId>/<version>.<ext>
        即 E:\\测试项目\\SeedDMS\\data\\1048576\\<docId>\\1.xlsx
  - 常量：M_READ=2（defaultAccess）、S_RELEASED=2（初始状态）
  - folderList：祖先 id 串，形如 ":1:2:"
  - checksum：文件 md5（32 位 hex 小写）

注意：
  - 这是「绕过 SeedDMS 应用层」的直接写库，需保证与 SeedDMS 的字段契约一致，
    否则 SeedDMS Web UI 可能显示异常。字段值、类型、目录规则已逐一核对源码。
  - 写入后 SeedDMS 的 lucene 全文索引不会自动更新（SeedDMS 原生上传会调 indexer）。
    伏羲本体的检索不依赖 SeedDMS 的 lucene，故可忽略；但 SeedDMS UI 内的
    全文搜索可能查不到新文档，属已知局限。
"""
import logging
import hashlib
import sqlite3
import time
import mimetypes
from pathlib import Path

from config import SEEDDMS_DB_PATH, SEEDDMS_CONTENT_DIR

logger = logging.getLogger("rag.dms.writer")

# SeedDMS 常量（与 inc.AccessUtils.php / inc.ClassDocument.php 一致）
M_READ = 2
S_RELEASED = 2


class SeedDMSWriteError(Exception):
    """SeedDMS 写库失败（配置缺失 / 数据库不可写 / 目录不存在等）"""


def _content_offset_dir() -> Path:
    """文档内容根目录（含 contentOffsetDir，即 1048576 层）"""
    return Path(SEEDDMS_CONTENT_DIR)


def get_dms_conn() -> sqlite3.Connection:
    """获取 SeedDMS content.db 连接（读配置，每次新建，多线程安全）"""
    db_path = Path(SEEDDMS_DB_PATH)
    if not db_path.exists():
        raise SeedDMSWriteError(f"SeedDMS 数据库不存在: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _detect_mime_and_ext(filename: str) -> tuple[str, str]:
    """根据文件名推断 mimeType 与 fileType（带 . 的扩展名）"""
    ext = Path(filename).suffix  # 含点，如 ".xlsx"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return mime, ext


def get_folder_path_ids(conn: sqlite3.Connection, folder_id: int) -> list[int]:
    """返回某文件夹的祖先 id 链（从根到该文件夹，含自身）。

    用于拼 folderList（":1:2:"）和判定目标文件夹是否存在。
    """
    ids = []
    cur = folder_id
    guard = 0
    while cur is not None and guard < 50:
        row = conn.execute(
            "SELECT id, parent FROM tblFolders WHERE id=?", (cur,)
        ).fetchone()
        if row is None:
            break
        ids.append(row["id"])
        cur = row["parent"]
        guard += 1
    ids.reverse()
    return ids


def list_folders() -> list[dict]:
    """列出 SeedDMS 全部文件夹（用于前端「选择目标文件夹」下拉）。"""
    conn = get_dms_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, parent, folderList FROM tblFolders ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _folder_list_string(path_ids: list[int]) -> str:
    """由祖先 id 链拼 folderList（":1:2:"）"""
    if not path_ids:
        return ""
    return ":" + ":".join(str(i) for i in path_ids) + ":"


def create_document(conn: sqlite3.Connection, folder_id: int, filename: str,
                    content: bytes = None, owner_id: int = 1,
                    comment: str = "", keywords: str = "",
                    content_path: str = None) -> int:
    """在 SeedDMS 中创建文档（含首个版本），返回新 document_id。

    严格复刻 SeedDMS Folder::addDocument + Document::addContent 的写库顺序：
      1. tblDocuments 插入 → 拿 docId
      2. 文件落盘 contentDir/<docId>/1.<ext>
      3. tblDocumentContent 插入（dir="<docId>/", checksum=md5）
      4. tblDocumentStatus + tblDocumentStatusLog（S_RELEASED）

    #23（2026-09-21）：支持 content_path 流式写入（大文件不再全量驻留内存）。
      - content（bytes）：兼容旧调用；
      - content_path（str）：分块读临时文件 → 流式落盘 + 增量 md5，内存峰值降到块大小。

    返回新文档 id。失败抛 SeedDMSWriteError 并回滚。
    """
    # 校验目标文件夹存在
    path_ids = get_folder_path_ids(conn, folder_id)
    if not path_ids or path_ids[-1] != folder_id:
        raise SeedDMSWriteError(f"目标文件夹不存在: id={folder_id}")

    if content is None and content_path is None:
        raise SeedDMSWriteError("create_document 需提供 content 或 content_path")

    mime, ext = _detect_mime_and_ext(filename)
    now_ts = int(time.time())

    def _stream_copy_and_hash(src_path: str, dest_path: Path):
        """分块拷贝 + 增量 md5，返回 (size, md5_hex)。"""
        md5 = hashlib.md5()
        size = 0
        with open(src_path, "rb") as rf, open(dest_path, "wb") as wf:
            while True:
                buf = rf.read(4 * 1024 * 1024)
                if not buf:
                    break
                wf.write(buf)
                md5.update(buf)
                size += len(buf)
        return size, md5.hexdigest()

    if content is not None:
        file_size = len(content)
        checksum = hashlib.md5(content).hexdigest()  # SeedDMS 用 md5（32位小写 hex）
    else:
        # 惰性：落盘时才计算（下方 _stream_copy_and_hash）
        file_size = None
        checksum = None

    # SeedDMS 约定：tblDocuments.name 存「不含扩展名」的文档名，下载时用 name + fileType 拼接。
    # 若 name 已含扩展名，REST 下载 /restapi/document/{id}/content 的 filename 会重复（x.txt.txt）。
    # 因此：name = 去扩展名，orgFileName = 完整文件名，fileType = 带点的扩展名。
    doc_name = Path(filename).stem  # 去扩展名
    org_filename = filename          # 完整文件名（保留扩展名用于溯源）

    try:
        conn.execute("BEGIN")

        # 1) 文档元数据（name 去扩展名）
        cur = conn.execute(
            """
            INSERT INTO tblDocuments
                (name, comment, date, expires, owner, folder, folderList,
                 inheritAccess, defaultAccess, locked, keywords, sequence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (doc_name, comment, now_ts, 0, owner_id, folder_id,
             _folder_list_string(path_ids), 1, M_READ, -1, keywords, 0.0),
        )
        doc_id = cur.lastrowid

        # 2) 文件落盘
        doc_dir = _content_offset_dir() / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        version = 1
        dest = doc_dir / f"{version}{ext}"
        if content is not None:
            dest.write_bytes(content)
        else:
            # #23：流式落盘 + 增量 md5，避免大文件全量驻留内存
            file_size, checksum = _stream_copy_and_hash(content_path, dest)

        # 3) 版本内容记录（dir 是 "<docId>/"，SeedDMS getDir() 返回）
        dir_field = f"{doc_id}/"
        conn.execute(
            """
            INSERT INTO tblDocumentContent
                (document, version, comment, date, createdBy, dir, orgFileName,
                 fileType, mimeType, fileSize, checksum)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (doc_id, version, comment, now_ts, owner_id, dir_field, org_filename,
             ext, mime, file_size, checksum),
        )
        content_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        # 4) 状态表（documentID + version）
        conn.execute(
            "INSERT INTO tblDocumentStatus (documentID, version) VALUES (?,?)",
            (doc_id, version),
        )
        status_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        # 5) 状态日志（初始 S_RELEASED）
        conn.execute(
            """
            INSERT INTO tblDocumentStatusLog
                (statusID, status, comment, date, userID)
            VALUES (?,?,?,?,?)
            """,
            (status_id, S_RELEASED, "New document content submitted",
             time.strftime("%Y-%m-%d %H:%M:%S"), owner_id),
        )

        conn.execute("COMMIT")
        logger.info(f"SeedDMS 写入文档成功: doc_id={doc_id} name={filename} size={file_size}")
        return doc_id

    except Exception:
        conn.execute("ROLLBACK")
        # 尽力清理已落盘文件
        try:
            doc_dir = _content_offset_dir() / str(doc_id)
            if doc_dir.exists():
                import shutil
                shutil.rmtree(doc_dir, ignore_errors=True)
        except Exception:
            pass
        raise


def upload_document(folder_id: int, filename: str, content: bytes = None,
                    owner_id: int = 1, comment: str = "",
                    keywords: str = "", content_path: str = None) -> int:
    """浏览器上传入口：写入 SeedDMS 并返回新 document_id。

    #23：content（bytes）与 content_path（临时文件路径）二选一，后者流式落盘。
    """
    conn = get_dms_conn()
    try:
        return create_document(conn, folder_id, filename, content,
                               owner_id=owner_id, comment=comment,
                               keywords=keywords, content_path=content_path)
    finally:
        conn.close()


def document_exists(folder_id: int, filename: str) -> bool:
    """检查文件夹下是否已有同名文档（SeedDMS 默认不允许重名，除非开 enableDuplicateDocNames）。"""
    conn = get_dms_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM tblDocuments WHERE folder=? AND name=?",
            (folder_id, filename),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
