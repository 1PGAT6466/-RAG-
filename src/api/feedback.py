"""api/feedback.py — 对话反馈路由（赞/踩，阶段一：只落库）"""
import json as _json
import re as _re
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.auth.deps import get_current_user
from src.storage.feedback import add_feedback, remove_feedback

logger = logging.getLogger("rag.api.feedback")
router = APIRouter()


class FeedbackReq(BaseModel):
    kind: str = Field(..., pattern=r'^(up|down)$')
    query: str = Field("", max_length=2000)
    chunk_ids: list = Field(default_factory=list, max_length=200)
    conversation_id: int | None = None
    message_id: int | None = None


@router.post("/api/feedback")
def api_add_feedback(req: FeedbackReq, user=Depends(get_current_user)):
    """记录一条点赞/点踩反馈（阶段一：只落库，暂不影响检索排序）。

    chunk_ids 为本次回答引用的 chunk id 列表（供阶段二按 chunk 聚合惩罚用）。
    """
    user_id = user.get("user_id")
    # 归一化 chunk_ids：字段来自 sources.chunk_id / 结果 id，可能混入字符串
    cids = []
    for c in req.chunk_ids:
        if isinstance(c, int):
            cids.append(c)
        elif isinstance(c, str) and c.isdigit():
            cids.append(int(c))
    fid = add_feedback(user_id, req.kind, query=req.query, chunk_ids=cids,
                       conversation_id=req.conversation_id, message_id=req.message_id)
    return {"status": "ok", "data": {"id": fid, "kind": req.kind}}


@router.delete("/api/feedback")
def api_remove_feedback(kind: str, query: str = "", chunk_ids: str = "[]",
                              user=Depends(get_current_user)):
    """撤销反馈（点赞/点踩可取消）。chunk_ids 传 JSON 数组字符串。"""
    # S8: kind 参数校验（只能是 up/down）
    if not _re.match(r'^(up|down)$', kind):
        raise HTTPException(status_code=400, detail="kind 参数只能是 up 或 down")
    try:
        cids = _json.loads(chunk_ids or "[]")
    except Exception:
        cids = []
    ok = remove_feedback(user.get("user_id"), kind, query=query, chunk_ids=cids)
    return {"status": "ok", "data": {"removed": ok}}
