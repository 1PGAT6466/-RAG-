"""文件/文件夹/图片操作"""
import json
import logging

from src.storage.connection import _get_conn, _dumps

logger = logging.getLogger("rag.db.files")


# === 文件操作 ===

def add_file(name: str, path: str, ext: str = "", size: int = 0, folder: str = "/") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO files (name, path, ext, size, folder) VALUES (?,?,?,?,?)",
        (name, path, ext, size, normalize_folder(folder))
    )
    conn.commit()
    return cur.lastrowid


def get_file(file_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def list_files(category: str = None, date: str = None, folder: str = None) -> list[dict]:
    conn = _get_conn()
    # 日期筛选（近 N 天），date 形如 "7d"/"30d"/"90d"
    conds = []
    args = []
    if date:
        try:
            days = int(str(date).rstrip("d"))
            conds.append("updated_at >= datetime('now','localtime', ?)")
            args.append(f"-{days} days")
        except ValueError:
            pass
    if category:
        conds.append("category = ?")
        args.append(category)
    if folder is not None:
        f = normalize_folder(folder)
        if f == "/":
            # 根目录：仅未归入任何子目录的文件
            conds.append("folder = '/'")
        else:
            # 子目录：精确匹配或在其下的子目录（用前缀匹配）
            conds.append("(folder = ? OR folder LIKE ?)")
            args.append(f)
            args.append(f + "/%")
    where = " WHERE " + " AND ".join(conds) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM files{where} ORDER BY updated_at DESC", tuple(args)
    ).fetchall()
    return [dict(r) for r in rows]


def list_files_with_entities(category: str = None, model: str = None, material: str = None, date: str = None, folder: str = None) -> list[dict]:
    """文件列表 + 每个文件关联的实体（型号/材料），支持按型号/材料/日期筛选（阶段 3 字段筛选）

    返回 list_files 的结果，每个文件额外带：
      - models: list[str]   连接器型号实体（type=connector）
      - materials: list[str] 材料实体（type=material）
    """
    conn = _get_conn()
    files = list_files(category=category, date=date, folder=folder)
    if not files:
        return []
    file_ids = [f["id"] for f in files]
    placeholders = ",".join("?" * len(file_ids))

    # 聚合每个文件的型号 + 材料实体
    rows = conn.execute(
        f"""
        SELECT ef.file_id, e.type, e.name
        FROM entity_files ef
        JOIN entities e ON e.id = ef.entity_id
        WHERE ef.file_id IN ({placeholders}) AND e.type IN ('connector','material')
        ORDER BY ef.mention_count DESC
        """,
        tuple(file_ids),
    ).fetchall()

    # 组装
    file_map = {f["id"]: f for f in files}
    for f in files:
        f["models"] = []
        f["materials"] = []
    for r in rows:
        f = file_map.get(r["file_id"])
        if not f:
            continue
        if r["type"] == "connector":
            if r["name"] not in f["models"]:
                f["models"].append(r["name"])
        elif r["type"] == "material":
            if r["name"] not in f["materials"]:
                f["materials"].append(r["name"])

    # 按型号/材料过滤
    if model:
        files = [f for f in files if model in f["models"]]
    if material:
        files = [f for f in files if material in f["materials"]]
    return files


def update_file_category(file_id: int, category: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET category=?, updated_at=datetime('now','localtime') WHERE id=?",
        (category, file_id)
    )
    conn.commit()


def update_file_tags(file_id: int, tags: list[str]):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET tags=?, updated_at=datetime('now','localtime') WHERE id=?",
        (_dumps(tags), file_id)
    )
    conn.commit()


def update_file_summary(file_id: int, summary: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET summary=?, updated_at=datetime('now','localtime') WHERE id=?",
        (summary or "", file_id)
    )
    conn.commit()


def update_file_folder(file_id: int, folder: str):
    """移动文件到指定目录（虚拟目录，Obsidian 式文件夹树）"""
    conn = _get_conn()
    folder = normalize_folder(folder)
    conn.execute(
        "UPDATE files SET folder=?, updated_at=datetime('now','localtime') WHERE id=?",
        (folder, file_id)
    )
    conn.commit()


def normalize_folder(folder: str) -> str:
    """规范化目录路径：去首尾空格，确保以 / 开头、无末尾斜杠"""
    folder = (folder or "").strip().replace("\\", "/")
    if not folder:
        return "/"
    if not folder.startswith("/"):
        folder = "/" + folder
    folder = folder.rstrip("/")
    if not folder:
        return "/"
    return folder


def list_folders() -> list[dict]:
    """构建目录树：返回 [{ name, path, count, children: [...] }]，含文件计数"""
    conn = _get_conn()
    rows = conn.execute("SELECT folder FROM files").fetchall()
    tree = {"children": {}, "count": 0}
    for (folder,) in rows:
        folder = normalize_folder(folder)
        parts = [p for p in folder.split("/") if p]
        node = tree
        for i, p in enumerate(parts):
            full = "/" + "/".join(parts[: i + 1])
            if p not in node["children"]:
                node["children"][p] = {"children": {}, "count": 0, "path": full}
            node = node["children"][p]
        node["count"] += 1

    def _build(node):
        return [
            {
                "name": name,
                "path": child["path"],
                "count": child["count"],
                "children": _build(child),
            }
            for name, child in sorted(node["children"].items())
        ]

    return _build(tree)


def delete_file(file_id: int):
    conn = _get_conn()
    # 先收集需要清理的数据（在删除之前）
    chunk_ids = conn.execute("SELECT id FROM chunks WHERE file_id=?", (file_id,)).fetchall()
    img_paths = conn.execute("SELECT path FROM images WHERE file_id=?", (file_id,)).fetchall()

    # 1) 先删 ChromaDB 向量（原子性：Chroma 删失败则中止，避免孤儿向量）
    from src.storage.chroma_store import delete_where, _use_chroma
    chroma_ok = False
    try:
        if _use_chroma():
            delete_where(ids=[str(cid) for (cid,) in chunk_ids])
        chroma_ok = True
    except Exception as e:
        logger.error(f"ChromaDB 删除失败（中止删除，避免孤儿向量）: {e}")
        raise

    # 2) Chroma 删成功，再删 SQLite（FTS + chunks + files CASCADE）
    for (cid,) in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM chunks_fts_tri WHERE rowid=?", (cid,))
    conn.execute("DELETE FROM links WHERE source_id=? OR target_id=?", (file_id, file_id))
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()

    # 3) 删磁盘图片目录
    try:
        import shutil
        from config import IMAGES_DIR
        img_dir = IMAGES_DIR / str(file_id)
        if img_dir.exists():
            shutil.rmtree(str(img_dir), ignore_errors=True)
    except Exception as e:
        logger.warning(f"清理图片目录失败（已忽略）: {e}")


def sync_chunk_count(file_id: int):
    """同步文件的 chunk_count"""
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE file_id = ?) WHERE id = ?",
        (file_id, file_id)
    )
    conn.commit()


def update_file_doc_meta(file_id: int, doc_kind: str, authority: int):
    """写入文档类型 + 权威等级（元数据层，检索精准度消费）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET doc_kind=?, authority=?, updated_at=datetime('now','localtime') WHERE id=?",
        (doc_kind or "未分类", int(authority or 0), file_id)
    )
    conn.commit()


def get_file_authority(file_id: int) -> int:
    """读取文件的权威等级（缺省 0）"""
    conn = _get_conn()
    row = conn.execute("SELECT authority FROM files WHERE id=?", (file_id,)).fetchone()
    return int(row["authority"]) if row else 0


# === 图片操作 ===

def add_images(file_id: int, images: list[dict]) -> int:
    """批量插入图片记录，返回插入数量"""
    conn = _get_conn()
    n = 0
    for img in images:
        conn.execute(
            "INSERT INTO images (file_id, page, path, filename, width, height) VALUES (?,?,?,?,?,?)",
            (file_id, img.get("page", 0), img.get("path", ""),
             img.get("filename", ""), img.get("width", 0), img.get("height", 0))
        )
        n += 1
    conn.commit()
    return n


def list_images(file_id: int) -> list[dict]:
    """返回文件的图片列表（按 page 排序）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM images WHERE file_id=? ORDER BY page, id", (file_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def count_images(file_id: int) -> int:
    conn = _get_conn()
    return conn.execute("SELECT COUNT(*) FROM images WHERE file_id=?", (file_id,)).fetchone()[0]
