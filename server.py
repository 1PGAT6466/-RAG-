"""
更新RAG框架 — 服务入口
====================
知识库文件管理系统 v1.0
"""
import sys
import logging
import time
from pathlib import Path

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager

from config import HOST, PORT, CORS_ORIGINS
from src.storage.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rag")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库 + 插件系统"""
    logger.info("初始化数据库...")
    init_db()
    logger.info("数据库就绪")
    try:
        from src.storage.chroma_store import ensure_synced, _use_chroma
        if _use_chroma():
            ensure_synced()
    except Exception as e:
        logger.warning(f"Chroma 补齐失败（不影响主服务）: {e}")
    try:
        from src.plugins import init as init_plugins
        init_plugins()
    except Exception as e:
        logger.warning(f"插件系统初始化失败（不影响主服务）: {e}")
    yield
    # 关闭插件子进程
    try:
        from src.plugins.host import get_pool
        get_pool().stop_all()
    except Exception:
        pass
    logger.info("服务关闭")


app = FastAPI(
    title="更新RAG框架",
    description="工业知识库文件管理系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS（收敛到具体来源；不允许 * + credentials 的危险组合）
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === 请求日志 + 耗时（可观测性） ===
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 交给异常处理器，这里只记日志
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} "
        f"({elapsed_ms:.1f}ms)"
    )
    return response


# === 全局异常处理（服务平台：不返回裸 500 HTML） ===
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "detail": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理异常 {request.method} {request.url.path}: {exc}", exc_info=True)
    # 数据库锁/连接失败等基础设施错误：返回可读的 detail 而非笼统的「服务器内部错误」，
    # 便于前端定位是网络/服务/数据层问题（生产环境可另设开关隐藏内部细节）
    detail = "服务器内部错误"
    if isinstance(exc, __import__("sqlite3").OperationalError) and "locked" in str(exc).lower():
        detail = "数据库忙，请稍后重试"
    elif "LLM 服务" in str(exc) or "LLM 调用" in str(exc):
        detail = str(exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": detail},
    )

# 注册路由
from src.api import router
app.include_router(router)


# === 前端静态托管（服务平台：单端口一体化部署，serve frontend/dist） ===
_FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA 回退：非 /api、/assets 路径回退到 index.html"""
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        index = _FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        raise StarletteHTTPException(status_code=404, detail="前端未构建（运行 npm run build）")


if __name__ == "__main__":
    import uvicorn
    logger.info(f"启动服务: http://{HOST}:{PORT}")
    # 直接传 app 对象（而非字符串 "server:app"），避免 Windows 下模块路径缓存导致加载旧代码
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
