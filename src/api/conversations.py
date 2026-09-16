"""api/conversations.py — 会话管理路由"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.auth.deps import get_current_user
from src.storage.db import (
    list_conversations, create_conversation, get_conversation,
    get_conversation_messages, delete_conversation,
)

router = APIRouter()


class ConversationCreateReq(BaseModel):
    title: str = Field("新对话", max_length=100)


@router.get("/api/conversations")
def api_list_conversations(user=Depends(get_current_user)):
    """获取当前用户的会话列表"""
    return {"status": "ok", "data": list_conversations(user.get("user_id"))}


@router.post("/api/conversations")
def api_create_conversation(req: ConversationCreateReq, user=Depends(get_current_user)):
    """创建新会话"""
    cid = create_conversation(user.get("user_id"), req.title)
    return {"status": "ok", "data": {"id": cid, "title": req.title}}


@router.get("/api/conversations/{conversation_id}")
def api_get_conversation(conversation_id: int, user=Depends(get_current_user)):
    """获取会话详情（含所有消息）"""
    conv = get_conversation(conversation_id, user_id=user.get("user_id"))
    if not conv:
        raise HTTPException(404, "会话不存在")
    msgs = get_conversation_messages(conversation_id)
    return {"status": "ok", "data": {"conversation": conv, "messages": msgs}}


@router.delete("/api/conversations/{conversation_id}")
def api_delete_conversation(conversation_id: int, user=Depends(get_current_user)):
    """删除会话（含所有消息）"""
    conv = get_conversation(conversation_id, user_id=user.get("user_id"))
    if not conv:
        raise HTTPException(404, "会话不存在")
    delete_conversation(conversation_id)
    return {"status": "ok"}
