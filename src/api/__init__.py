"""api/__init__.py — 路由注册入口"""
from fastapi import APIRouter
from src.api.auth import router as auth_router
from src.api.documents import router as documents_router
from src.api.search import router as search_router
from src.api.chat import router as chat_router
from src.api.conversations import router as conversations_router
from src.api.graph import router as graph_router
from src.api.config import router as config_router
from src.api.dms import router as dms_router
from src.api.feedback import router as feedback_router
from src.api.wiki import router as wiki_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(documents_router)
router.include_router(search_router)
router.include_router(chat_router)
router.include_router(conversations_router)
router.include_router(graph_router)
router.include_router(config_router)
router.include_router(dms_router)
router.include_router(feedback_router)
router.include_router(wiki_router)

# 插件 + MCP 市场路由（保持原有注册方式）
from src import api_plugins
api_plugins.register(router)
from src import api_mcp
api_mcp.register(router)
