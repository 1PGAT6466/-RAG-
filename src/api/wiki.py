"""Wiki API — 页面 CRUD + 编译 + 双链 + 版本"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.auth.deps import get_current_user, require_admin

router = APIRouter()


class WikiPageCreate(BaseModel):
    title: str
    content_md: str = ""
    category: str = "未分类"
    summary: str = ""
    source_file_ids: list[int] = []
    entity_ids: list[int] = []
    compiled_by: str = "human"


class WikiPageUpdate(BaseModel):
    title: Optional[str] = None
    content_md: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None


class WikiLinkCreate(BaseModel):
    to_page_id: int
    link_type: str = "wiki"


@router.get("/api/wiki/pages")
def api_list_pages(category: str = None, status: str = None, user=Depends(get_current_user)):
    from src.storage.db import list_wiki_pages
    pages = list_wiki_pages(category=category, status=status)
    return {"status": "ok", "data": pages}


@router.get("/api/wiki/pages/{page_id}")
def api_get_page(page_id: int, user=Depends(get_current_user)):
    from src.storage.db import get_wiki_page, get_wiki_links, get_wiki_backlinks, get_wiki_versions
    page = get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "页面不存在")
    page["links"] = get_wiki_links(page_id)
    page["backlinks"] = get_wiki_backlinks(page_id)
    page["versions"] = get_wiki_versions(page_id)[:10]  # 最近 10 个版本
    return {"status": "ok", "data": page}


@router.post("/api/wiki/pages")
def api_create_page(req: WikiPageCreate, user=Depends(require_admin)):
    from src.storage.db import create_wiki_page
    page_id = create_wiki_page(
        title=req.title, content_md=req.content_md, category=req.category,
        summary=req.summary, source_file_ids=req.source_file_ids,
        entity_ids=req.entity_ids, compiled_by=req.compiled_by,
    )
    return {"status": "ok", "data": {"id": page_id}}


@router.put("/api/wiki/pages/{page_id}")
def api_update_page(page_id: int, req: WikiPageUpdate, user=Depends(require_admin)):
    from src.storage.db import update_wiki_page
    ok = update_wiki_page(
        page_id, title=req.title, content_md=req.content_md,
        category=req.category, summary=req.summary, status=req.status,
        compiled_by="human",  # 人工编辑
    )
    if not ok:
        raise HTTPException(404, "页面不存在")
    return {"status": "ok"}


@router.delete("/api/wiki/pages/{page_id}")
def api_delete_page(page_id: int, user=Depends(require_admin)):
    from src.storage.db import delete_wiki_page
    ok = delete_wiki_page(page_id)
    if not ok:
        raise HTTPException(404, "页面不存在")
    return {"status": "ok"}


@router.post("/api/wiki/pages/{page_id}/links")
def api_add_link(page_id: int, req: WikiLinkCreate, user=Depends(require_admin)):
    from src.storage.db import add_wiki_link
    ok = add_wiki_link(page_id, req.to_page_id, req.link_type)
    if not ok:
        raise HTTPException(400, "添加链接失败")
    return {"status": "ok"}


@router.get("/api/wiki/stale")
def api_stale_pages(user=Depends(get_current_user)):
    from src.storage.db import get_wiki_stale_pages
    return {"status": "ok", "data": get_wiki_stale_pages()}


@router.post("/api/wiki/pages/{page_id}/compile")
def api_compile_page(page_id: int, user=Depends(require_admin)):
    """触发页面重编译（异步，当前简单实现：同步调用 LLM）"""
    from src.storage.db import get_wiki_page
    page = get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "页面不存在")
    # TODO: 异步编译 Stage（当前简化：返回提示）
    return {"status": "ok", "data": {"message": "编译任务已提交（暂未实现异步 Stage）"}}
