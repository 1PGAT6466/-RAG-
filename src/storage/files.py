"""文件/文件夹/图片操作"""
import json
import logging

from src.storage.connection import _get_conn, _dumps

logger = logging.getLogger("rag.db.files")


# === 文件操作 ===

def add_file(name: str, path: str, ext: str = "", size: int = 0, folder: str = "/", content_hash: str = None) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO files (name, path, ext, size, folder, content_hash) VALUES (?,?,?,?,?,?)",
        (name, path, ext, size, normalize_folder(folder), content_hash)
    )
    conn.commit()
    return cur.lastrowid


def get_file_by_hash(content_hash: str) -> dict | None:
    """按内容哈希查找未删除的文件（P22 去重）"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM files WHERE content_hash=? AND deleted_at IS NULL", (content_hash,)
    ).fetchone()
    return dict(row) if row else None


def update_file_hash(file_id: int, content_hash: str):
    """更新文件的 content_hash（P22，入库后回填）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE id = ?",
        (content_hash, file_id)
    )
    conn.commit()


def get_file(file_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM files WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()
    return dict(row) if row else None


def list_files(category: str = None, date: str = None, folder: str = None) -> list[dict]:
    conn = _get_conn()
    # 日期筛选（近 N 天），date 形如 "7d"/"30d"/"90d"
    conds = ["deleted_at IS NULL"]
    args = []
    if date:
        try:
            days = int(str(date).rstrip("d"))
            if days < 1 or days > 365:
                logger.warning(f"list_files: date 参数超出范围: {date}")
            else:
                conds.append("updated_at >= datetime('now','localtime', ?)")
                args.append(f"-{days} days")
        except ValueError:
            logger.warning(f"list_files: 非法 date 参数: {date}")
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
    rows = conn.execute(
        "SELECT folder, COUNT(*) as cnt FROM files WHERE deleted_at IS NULL GROUP BY folder"
    ).fetchall()
    tree = {"children": {}, "count": 0}
    for row in rows:
        folder = row[0] if isinstance(row, tuple) else row["folder"]
        cnt = row[1] if isinstance(row, tuple) else row["cnt"]
        folder = normalize_folder(folder)
        parts = [p for p in folder.split("/") if p]
        node = tree
        for i, p in enumerate(parts):
            full = "/" + "/".join(parts[: i + 1])
            if p not in node["children"]:
                node["children"][p] = {"children": {}, "count": 0, "path": full}
            node = node["children"][p]
        node["count"] += cnt

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
    """软删除：设置 deleted_at，数据保留可恢复（P23）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE files SET deleted_at = datetime('now','localtime') WHERE id = ?",
        (file_id,)
    )
    conn.commit()


def restore_file(file_id: int) -> bool:
    """从回收站恢复文件（P23）"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE files SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (file_id,)
    )
    conn.commit()
    return cur.rowcount > 0


def permanent_delete_file(file_id: int):
    """永久删除：清除所有数据（Chroma + SQLite + 磁盘）（P23）"""
    conn = _get_conn()
    chunk_ids = conn.execute("SELECT id FROM chunks WHERE file_id=?", (file_id,)).fetchall()

    # 1) 删 ChromaDB 向量
    from src.storage.chroma_store import delete_where, _use_chroma
    try:
        if _use_chroma():
            delete_where(ids=[str(cid) for (cid,) in chunk_ids])
    except Exception as e:
        logger.error(f"ChromaDB 删除失败: {e}")
        raise

    # 2) 删 SQLite（W2: 事务包裹，防止中途异常导致脏数据）
    cid_list = [cid for (cid,) in chunk_ids]
    with conn:
        # 清理实体关联（先于 chunks 删除，避免外键冲突）
        if cid_list:
            ph = ",".join("?" * len(cid_list))
            conn.execute(f"DELETE FROM entity_chunks WHERE chunk_id IN ({ph})", cid_list)
        conn.execute("DELETE FROM entity_files WHERE file_id=?", (file_id,))
        # 清理 links
        conn.execute("DELETE FROM links WHERE source_id=? OR target_id=?", (file_id, file_id))
        # 清理 chunks + FTS
        for cid in cid_list:
            conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            conn.execute("DELETE FROM chunks_fts_tri WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    # 3) 删磁盘图片
    try:
        import shutil
        from config import IMAGES_DIR
        img_dir = IMAGES_DIR / str(file_id)
        if img_dir.exists():
            shutil.rmtree(str(img_dir), ignore_errors=True)
    except Exception as e:
        logger.warning(f"清理图片目录失败: {e}")


def list_deleted_files() -> list[dict]:
    """列出回收站中的文件（P23）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM files WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def cleanup_old_deleted(days: int = 30):
    """清理超过 N 天的已删除文件（P23 自动清）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id FROM files WHERE deleted_at < datetime('now','localtime', ?)",
        (f"-{days} days",)
    ).fetchall()
    for (file_id,) in rows:
        permanent_delete_file(file_id)
    return len(rows)


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
    rows = [
        (file_id, img.get("page", 0), img.get("path", ""),
         img.get("filename", ""), img.get("width", 0), img.get("height", 0))
        for img in images
    ]
    if rows:
        conn.executemany(
            "INSERT INTO images (file_id, page, path, filename, width, height) VALUES (?,?,?,?,?,?)",
            rows,
        )
    conn.commit()
    return len(rows)


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
