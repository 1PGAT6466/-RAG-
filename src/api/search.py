"""api/search.py — 搜索路由"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.auth.deps import get_current_user
from src.retrieval.search import search

logger = logging.getLogger("rag.api.search")
router = APIRouter()


class SearchReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=50)

class FindFileReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


@router.post("/api/search")
async def api_search(req: SearchReq, user=Depends(get_current_user)):
    results = await search(req.query, top_k=req.top_k)
    return {"status": "ok", "data": results}


@router.post("/api/search/debug")
async def api_search_debug(req: SearchReq, user=Depends(get_current_user)):
    """检索命中测试（调试面板）：返回四路召回明细 + 融合 + rerank 后最终结果。

    对标 Dify 知识库「召回测试」：让管理员/用户能客观看到每个 chunk 在哪一路
    召回、融合前排名、rerank 后变化，判断知识库是否真正吃进了目标内容。
    复用 search(collect_detail=True)，不触碰主检索语义。
    """
    detail = await search(req.query, top_k=req.top_k, collect_detail=True)
    return {"status": "ok", "data": detail}


@router.post("/api/files/find")
async def api_find_files(req: FindFileReq, user=Depends(get_current_user)):
    """AI 智能找文件：语义检索 → 聚合到文件级 + LLM 推荐理由"""
    results = await search(req.query, top_k=30)
    if not results:
        return {"status": "ok", "data": [], "message": "未找到相关文件"}
    # 聚合到文件级
    file_map = {}
    for r in results:
        fid = r.get("file_id")
        if not fid:
            continue
        if fid not in file_map:
            file_map[fid] = {
                "file_id": fid,
                "file_name": r.get("file_name") or "",
                "score": r.get("score", 0.0),
                "hits": 0,
                "snippets": [],
            }
        entry = file_map[fid]
        entry["hits"] += 1
        entry["score"] = max(entry["score"], r.get("score", 0.0))
        if len(entry["snippets"]) < 2:
            snippet = (r.get("content") or "")[:120]
            if snippet:
                entry["snippets"].append(snippet)
                entry["snippet_chunk_index"] = r.get("chunk_index", 0)
    files = sorted(file_map.values(), key=lambda x: (x["hits"], x["score"]), reverse=True)[:8]
    reasons = await _generate_find_reasons(req.query, files)
    for i, f in enumerate(files):
        f["reason"] = reasons.get(i, "")
    return {"status": "ok", "data": files}


async def _generate_find_reasons(query: str, files: list[dict]) -> dict:
    """为找文件结果生成推荐理由，委托 src.llm。"""
    if not files:
        return {}
    try:
        from src.llm import call_llm, extract_json
        brief = "\n".join(
            f"{i+1}. {f['file_name']} —— 命中片段：{f['snippets'][0] if f['snippets'] else ''}"
            for i, f in enumerate(files)
        )
        prompt = (
            f"用户想找：「{query}」\n\n"
            f"检索到的候选文件：\n{brief}\n\n"
            f"请为每个文件用一句话（不超过20字）说明为什么它符合用户需求。"
            f"严格输出 JSON 对象，key 为文件名，value 为推荐理由，不要任何额外说明。"
        )
        raw = await call_llm([
            {"role": "system", "content": "你是文件检索助手，输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ])
        obj = extract_json(raw, expect="object")
        if not obj:
            return {}
        by_name = {f["file_name"]: i for i, f in enumerate(files)}
        return {by_name[name]: str(reason) for name, reason in obj.items() if name in by_name}
    except Exception as e:
        logger.warning(f"找文件推荐理由生成失败（已忽略）: {e}")
        return {}
