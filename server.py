"""
更新RAG框架 — 服务入口
====================
知识库文件管理系统 v1.0
"""
import os
os.environ.setdefault("PYTHONUTF8", "1")  # Windows 中文路径兼容

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

# 结构化日志（控制台可读 + 文件 JSON 按日轮转），取代 bare basicConfig
from src.logging_setup import setup_logging
setup_logging()
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
    # 恢复上次中断的入库任务状态
    try:
        from src.pipeline.engine import recover_tasks
        recover_tasks()
    except Exception as e:
        logger.warning(f"任务恢复失败（不影响主服务）: {e}")
    # 清理脏数据：超龄已完成任务 + test 残留 + 取消的任务
    try:
        from src.storage.tasks import cleanup_stale_tasks
        cleanup_stale_tasks()
    except Exception as e:
        logger.warning(f"任务脏数据清理失败（不影响主服务）: {e}")
    # 对账：Chroma 孤儿向量自动清理（历史脏数据/杀进程残留）
    try:
        from src.storage.chroma_store import _use_chroma
        if _use_chroma():
            from scripts.cleanup_chroma_orphans import main as cleanup_orphans
            cleanup_orphans()
    except Exception as e:
        logger.warning(f"Chroma 孤儿对账失败（不影响主服务）: {e}")
    # 加载语义缓存
    try:
        from src.chat.cache import load_cache
        load_cache()
    except Exception as e:
        logger.warning(f"语义缓存加载失败（不影响主服务）: {e}")
    # 预热 MCP 市场本地缓存（后台线程，不阻塞启动；未就绪时市场首次降级远程）
    try:
        from src.mcp.local_cache import refresh_cache
        refresh_cache(force=False)
    except Exception as e:
        logger.warning(f"MCP 缓存预热失败（不影响主服务）: {e}")
    # 预热本地 Embedding 模型（后台线程加载，避免首个检索请求冷启动阻塞 7s+）
    try:
        _warmup_embedder()
    except Exception as e:
        logger.warning(f"Embedding 预热失败（不影响主服务）: {e}")
    yield
    # 关闭插件子进程
    try:
        from src.plugins.host import get_pool
        get_pool().stop_all()
    except Exception:
        pass
    logger.info("服务关闭")


def _warmup_embedder():
    """后台线程预热本地 Embedding 模型，避免首个检索请求冷启动阻塞。

    用一条短文本触发 SentenceTransformer 首次加载（模型常驻内存），
    之后 encode_query 直接命中已加载的模型，无需再等待加载。
    """
    import threading

    def _load():
        try:
            from src.pipeline.embedder import encode_query
            encode_query("预热")
            logger.info("Embedding 模型预热完成")
        except Exception as e:
            logger.warning(f"Embedding 预热后台加载失败（首个请求会走冷启动）: {e}")

    t = threading.Thread(target=_load, name="embedder-warmup", daemon=True)
    t.start()


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


# === 安全响应头（内网部署硬化：防 MIME 嗅探 / 防点击劫持 / 禁用浏览端缓存敏感数据） ===
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    return response


# === 请求级 SQLite 连接隔离（async 协程安全） ===
@app.middleware("http")
async def db_connection_per_request(request: Request, call_next):
    from src.storage.db import _new_conn, set_conn, reset_conn
    conn = _new_conn()
    set_conn(conn)
    try:
        return await call_next(request)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        # 清除 contextvar 标记，避免连接对象在请求结束后仍被后续协程误用
        reset_conn()


# === 请求日志 + 耗时 + 慢查询告警（可观测性） ===
import uuid as _uuid
_SLOW_MS = 3000  # 超过此耗时的请求打 WARNING（便于定位慢查询）


@app.middleware("http")
async def request_logger(request: Request, call_next):
    # 请求 ID：优先取上游传入的 X-Request-ID，否则生成一个（可观测性/链路追踪）
    req_id = request.headers.get("X-Request-ID") or _uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 交给异常处理器，这里只记日志
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = req_id
    # 结构化字段：写入 logger 的 extra，供 JSON 格式器采集（控制台格式器忽略）
    extra = {
        "trace_id": req_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    # P25: 记录请求指标
    try:
        from src.metrics import record_request
        record_request(path=request.url.path, status=response.status_code, latency_ms=elapsed_ms)
    except Exception:
        pass

    if elapsed_ms > _SLOW_MS:
        logger.warning(
            f"[慢请求 {elapsed_ms:.0f}ms] {request.method} {request.url.path} "
            f"-> {response.status_code} (id={req_id})",
            extra=extra,
        )
    else:
        logger.info(
            f"{request.method} {request.url.path} -> {response.status_code} "
            f"({elapsed_ms:.1f}ms, id={req_id})",
            extra=extra,
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


# === 图片静态托管（可读模式 ![[图]] 显示） ===
_IMAGES_DIR = Path(__file__).parent / "data" / "images"
if _IMAGES_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/images", StaticFiles(directory=str(_IMAGES_DIR)), name="images")

# === 前端静态托管（服务平台：单端口一体化部署，serve frontend/dist） ===
_FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA 回退：非 /api、/assets、/images 路径回退到 index.html。
        已知静态文件（如 pdf-viewer.html）直接从 dist 提供，不走 SPA。"""
        if full_path.startswith("api/") or full_path.startswith("assets/") or full_path.startswith("images/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        # 已知静态文件直接提供
        static_file = _FRONTEND_DIST / full_path
        if static_file.is_file() and full_path.endswith((".html", ".js", ".css", ".json", ".svg", ".ico")):
            return FileResponse(static_file)
        index = _FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        raise StarletteHTTPException(status_code=404, detail="前端未构建（运行 npm run build）")


if __name__ == "__main__":
    import uvicorn
    logger.info(f"启动服务: http://{HOST}:{PORT}")
    # 直接传 app 对象（而非字符串 "server:app"），避免 Windows 下模块路径缓存导致加载旧代码
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info", workers=1)
