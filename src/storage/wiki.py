"""
wiki.py — Wiki 页面 CRUD + 版本 + 双链（02 文档 M1）
"""
import json
import logging
import re

from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.wiki")


def _slugify(title: str) -> str:
    """生成稳定 slug（中文用拼音或保留，英文小写+连字符）"""
    s = title.strip().lower()
    s = re.sub(r'[^\w\u4e00-\u9fff]+', '-', s)
    s = s.strip('-')
    return s or 'untitled'


def create_page(title: str, content_md: str = "", category: str = "未分类",
                summary: str = "", source_file_ids: list = None,
                entity_ids: list = None, compiled_by: str = "llm",
                slug: str = None) -> int:
    """创建 Wiki 页面，返回 page_id"""
    conn = _get_conn()
    if not slug:
        slug = _slugify(title)
    # 确保 slug 唯一
    base_slug = slug
    counter = 1
    while conn.execute("SELECT id FROM wiki_pages WHERE slug=?", (slug,)).fetchone():
        slug = f"{base_slug}-{counter}"
        counter += 1

    cur = conn.execute(
        "INSERT INTO wiki_pages (slug, title, category, content_md, summary,"
        " source_file_ids, entity_ids, compiled_by) VALUES (?,?,?,?,?,?,?,?)",
        (slug, title, category, content_md, summary,
         json.dumps(source_file_ids or [], ensure_ascii=False),
         json.dumps(entity_ids or [], ensure_ascii=False),
         compiled_by)
    )
    page_id = cur.lastrowid
    # 记录版本
    conn.execute(
        "INSERT INTO wiki_versions (page_id, version, content_md, changed_by) VALUES (?,?,?,?)",
        (page_id, 1, content_md, compiled_by)
    )
    conn.commit()
    logger.info(f"Wiki 页面创建: id={page_id} slug={slug} title={title}")
    return page_id


def get_page(page_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM wiki_pages WHERE id=?", (page_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["source_file_ids"] = json.loads(d.get("source_file_ids") or "[]")
    d["entity_ids"] = json.loads(d.get("entity_ids") or "[]")
    return d


def get_page_by_slug(slug: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM wiki_pages WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["source_file_ids"] = json.loads(d.get("source_file_ids") or "[]")
    d["entity_ids"] = json.loads(d.get("entity_ids") or "[]")
    return d


def list_pages(category: str = None, status: str = None) -> list[dict]:
    conn = _get_conn()
    sql = "SELECT id, slug, title, category, summary, status, compiled_by, version, updated_at FROM wiki_pages"
    conds = []
    params = []
    if category:
        conds.append("category=?")
        params.append(category)
    if status:
        conds.append("status=?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY updated_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_page(page_id: int, title: str = None, content_md: str = None,
                category: str = None, summary: str = None,
                status: str = None, compiled_by: str = None) -> bool:
    """更新页面（写版本历史）"""
    conn = _get_conn()
    page = get_page(page_id)
    if not page:
        return False

    updates = []
    params = []
    if title is not None:
        updates.append("title=?")
        params.append(title)
    if content_md is not None:
        updates.append("content_md=?")
        params.append(content_md)
    if category is not None:
        updates.append("category=?")
        params.append(category)
    if summary is not None:
        updates.append("summary=?")
        params.append(summary)
    if status is not None:
        updates.append("status=?")
        params.append(status)
    if compiled_by is not None:
        updates.append("compiled_by=?")
        params.append(compiled_by)

    if not updates:
        return False

    updates.append("updated_at=datetime('now')")
    updates.append("version=version+1")
    params.append(page_id)
    conn.execute(f"UPDATE wiki_pages SET {', '.join(updates)} WHERE id=?", params)

    # 记录版本（从 DB 读取更新后的 version，避免并发更新时 Python 变量计算的版本号重复）
    updated_page = conn.execute("SELECT version FROM wiki_pages WHERE id=?", (page_id,)).fetchone()
    new_version = updated_page["version"] if updated_page else page["version"] + 1
    conn.execute(
        "INSERT INTO wiki_versions (page_id, version, content_md, changed_by) VALUES (?,?,?,?)",
        (page_id, new_version, content_md or page.get("content_md", ""),
         compiled_by or page.get("compiled_by", "llm"))
    )
    conn.commit()
    return True


def delete_page(page_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM wiki_pages WHERE id=?", (page_id,))
    conn.commit()
    return cur.rowcount > 0


# 双链
def add_link(from_page_id: int, to_page_id: int, link_type: str = "wiki") -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO wiki_links (from_page_id, to_page_id, link_type) VALUES (?,?,?)",
            (from_page_id, to_page_id, link_type)
        )
        conn.commit()
        return True
    except Exception:
        return False


def get_links(page_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT wl.*, wp.title as to_title FROM wiki_links wl"
        " JOIN wiki_pages wp ON wp.id=wl.to_page_id"
        " WHERE wl.from_page_id=?",
        (page_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_backlinks(page_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT wl.*, wp.title as from_title FROM wiki_links wl"
        " JOIN wiki_pages wp ON wp.id=wl.from_page_id"
        " WHERE wl.to_page_id=?",
        (page_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# 版本历史
def get_versions(page_id: int) -> list[dict]:
    conn = _get_conn()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM wiki_versions WHERE page_id=? ORDER BY version DESC",
        (page_id,)
    ).fetchall()]


def get_stale_pages() -> list[dict]:
    """获取需要重编译的页面"""
    conn = _get_conn()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM wiki_pages WHERE status='stale' ORDER BY updated_at"
    ).fetchall()]
