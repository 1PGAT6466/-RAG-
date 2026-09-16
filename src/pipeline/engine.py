"""
engine.py — 入库引擎核心（统一编排）
=====================================

设计原则（对齐"伏羲"新框架目标）：
  1. 统一   ：所有入库路径（HTTP上传 / 文件夹监控 / cron / API重跑）都走这一个引擎
  2. 有序   ：Stage 按固定顺序执行，每步状态可观测、可上报
  3. 稳定   ：每个 Stage 独立 try/except，单点失败降级不阻断整条链路
  4. 简洁   ：Stage 就是普通函数，不做 plugin_analyzer/auto_integrator 那套过度设计
  5. 快速   ：同步基础链（解析/分块/向量化/规则抽取）零 LLM 快速完成，
             LLM 密集型（摘要/标签/预索引）异步后台执行

Stage 分两类：
  - SYNC_STAGES  ：入库必跑、快速、零 LLM，失败只降级不阻断
  - ASYNC_STAGES ：后处理增强（摘要/标签/预索引），LLM 密集，丢后台线程执行

并发控制：
  - RAG_INGEST_MAX_CONCURRENT（默认 3）：入库信号量，避免批量上传 OOM
  - _wait_for_file：无进展超时（progress/stage/chunks 变即重置计时），大文档不误判

任务状态机（P20 增强）：
  pending → running → done / failed / retrying
  retrying → pending（退避到期后自动回到 pending → running）
  重试耗尽 → dead_letter=1（死信队列，可查不可自动重试）

断点续跑：
  每个 sync Stage 完成后保存 checkpoint（stage名 + 上下文数据），
  服务重启后从 checkpoint 位置续跑，不从头来。

大规模文档路径：
  PDF ≥ 20 页走流式（parse_pdf_streaming），逐页解析+增量 embed，避免 OOM
  chunk 数 > EMBED_REMOTE_THRESHOLD(300) 自动切远程 embedding API
"""
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx

from config import UPLOAD_DIR, RAG_STREAM_INGEST, RAG_STREAM_MIN_PAGES, RAG_STREAM_FLUSH_PAGES, RAG_INGEST_MAX_CONCURRENT

logger = logging.getLogger("rag.engine")

# ============================================================
# 任务状态机（P20 增强）
# ============================================================
_tasks: dict[str, dict] = {}
_lock = threading.Lock()
_ingest_semaphore = threading.BoundedSemaphore(max(1, RAG_INGEST_MAX_CONCURRENT))
_MAX_TASKS = 1000

# 重试参数（与 tasks.py 一致）
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 5.0   # 首次重试延迟（秒）
_RETRY_BACKOFF = 2.0       # 退避倍数


def _is_transient_error(exc: Exception) -> bool:
    """判断是否为瞬时错误（值得重试）。

    瞬时：LLM 超时、网络抖动、429 限流、连接重置
    持久：文件不存在、解析失败、内容为空、格式错误
    """
    msg = str(exc).lower()
    # 瞬时错误关键词
    transient_keywords = [
        "timeout", "超时", "timed out",
        "429", "rate limit", "限流",
        "connection", "连接", "reset", "eof",
        "502", "503", "504",
        "temporary", "暂时",
    ]
    for kw in transient_keywords:
        if kw in msg:
            return True
    # 某些异常类型本身就是瞬时的
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, ConnectionError, TimeoutError)):
        return True
    return False


def _evict_old_tasks():
    if len(_tasks) <= _MAX_TASKS:
        return
    finished = [(tid, t) for tid, t in _tasks.items()
                if t.get("status") in ("done", "failed")]
    finished.sort(key=lambda x: x[1].get("created_at", 0))
    overflow = len(_tasks) - _MAX_TASKS
    for tid, _ in finished[:overflow]:
        _tasks.pop(tid, None)
        try:
            from src.storage.db import delete_task
            delete_task(tid)
        except Exception:
            pass
        logger.info(f"[引擎] 淘汰历史任务 {tid}（内存上限 {_MAX_TASKS}）")


def _emit(task_id: str, stage: str, progress: int, text: str):
    """更新任务状态（线程安全），并持久化"""
    t = None
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t.update(status="running", stage=stage, progress=progress, progress_text=text)
    if t:
        try:
            from src.storage.db import save_task
            save_task(t)
        except Exception as e:
            logger.warning(f"[引擎] 任务持久化失败（已忽略）: {e}")


def _save_checkpoint(task_id: str, stage: str, data: dict = None):
    """保存 Stage 断点（每完成一个 sync Stage 调用一次）"""
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t["checkpoint_stage"] = stage
            t["checkpoint_data"] = data or {}
    try:
        from src.storage.db import save_checkpoint
        save_checkpoint(task_id, stage, data or {})
    except Exception as e:
        logger.warning(f"[引擎] 断点保存失败（已忽略）: {e}")


# ============================================================
# Stage 注册表
# ============================================================
_sync_stages: list[tuple[str, Callable]] = []
_async_stages: list[tuple[str, Callable]] = []
_critical_stages: dict[str, bool] = {}


def register_stage(name: str, stage_type: str = "sync", critical: bool = False):
    if critical:
        _critical_stages[name] = True
    def deco(fn: Callable):
        if stage_type == "async":
            _async_stages.append((name, fn))
        else:
            _sync_stages.append((name, fn))
        return fn
    return deco


# ============================================================
# 引擎入口
# ============================================================
def enqueue(filepath: str, filename: str = None) -> str:
    """把文件喂给引擎，返回 task_id"""
    task_id = uuid.uuid4().hex[:8]
    src = Path(filepath)
    if filename is None:
        filename = src.name
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "filename": filename,
            "filepath": str(filepath),
            "status": "pending",
            "stage": "",
            "progress": 0,
            "progress_text": "等待处理...",
            "chunks": 0,
            "category": "",
            "file_id": None,
            "summary": None,
            "tags": [],
            "error": None,
            "created_at": time.time(),
            # P20: 重试字段
            "retry_count": 0,
            "max_retries": _MAX_RETRIES,
            "next_retry_at": 0,
            "dead_letter": 0,
            "checkpoint_stage": "",
            "checkpoint_data": {},
        }
        _evict_old_tasks()
    try:
        from src.storage.db import save_task
        save_task(_tasks[task_id])
    except Exception as e:
        logger.warning(f"[引擎] 任务持久化失败（已忽略）: {e}")
    t = threading.Thread(target=_run_with_limit, args=(task_id,), daemon=True)
    t.start()
    logger.info(f"[引擎] 任务入队: {task_id} → {filename}")
    return task_id


def _run_with_limit(task_id: str):
    _ingest_semaphore.acquire()
    try:
        _run(task_id)
    finally:
        _ingest_semaphore.release()


def get_status(task_id: str) -> Optional[dict]:
    with _lock:
        t = _tasks.get(task_id)
    if t:
        return dict(t)
    # W1: 内存无此 task，从 SQLite 补查（服务重启后已完成任务）
    try:
        from src.storage.tasks import load_task
        row = load_task(task_id)
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def list_tasks() -> list[dict]:
    with _lock:
        return [dict(v) for v in _tasks.values()]


def _run(task_id: str):
    """引擎主循环：按顺序跑 sync stages，支持断点续跑"""
    with _lock:
        t = _tasks[task_id]
    ctx = {
        "task_id": task_id,
        "filepath": t["filepath"],
        "filename": t["filename"],
        "emit": lambda stage, progress, text: _emit(task_id, stage, progress, text),
    }

    # P20: 断点续跑 — 如果有 checkpoint，从 checkpoint 之后的 Stage 开始
    checkpoint_stage = t.get("checkpoint_stage", "")
    checkpoint_data = t.get("checkpoint_data", {})
    if checkpoint_stage and checkpoint_data:
        ctx.update(checkpoint_data)
        logger.info(f"[引擎] 从断点续跑: {task_id} → checkpoint={checkpoint_stage}")

    try:
        _emit(task_id, "prepare", 2, "准备文件...")
        if not checkpoint_stage:
            _prepare(ctx)
        else:
            # 续跑时复用已有 target_path
            ctx["target_path"] = checkpoint_data.get("target_path", t["filepath"])
            ctx["ext"] = checkpoint_data.get("ext", Path(t["filepath"]).suffix.lower())

        # 流式路径：大 PDF 走边解析边入库
        if ctx["ext"] == ".pdf" and _use_streaming(ctx):
            _run_streaming_pdf(task_id, ctx)
        else:
            _run_sync(task_id, ctx, skip_until=checkpoint_stage)
    except Exception as e:
        import traceback
        logger.error(f"[引擎] 任务失败 {task_id}: {e}\n{traceback.format_exc()}")
        _handle_failure(task_id, t, e)
    finally:
        _cleanup_tmp(ctx.get("filepath", t.get("filepath", "")))


def _handle_failure(task_id: str, task: dict, error: Exception):
    """P20: 失败处理 — 瞬时错误自动重试，持久错误直接 failed。

    指数退避：5s → 10s → 20s，超过 max_retries 进死信队列。
    """
    retry_count = task.get("retry_count", 0)
    max_retries = task.get("max_retries", _MAX_RETRIES)

    if _is_transient_error(error) and retry_count < max_retries:
        # 瞬时错误 + 还有重试次数 → 调度重试
        delay = _RETRY_BASE_DELAY * (_RETRY_BACKOFF ** retry_count)
        next_retry_at = time.time() + delay
        new_retry_count = retry_count + 1

        with _lock:
            task.update(
                status="retrying",
                retry_count=new_retry_count,
                error=str(error),
                next_retry_at=next_retry_at,
                progress_text=f"瞬时错误，{delay:.0f}s 后第 {new_retry_count} 次重试",
            )

        try:
            from src.storage.db import mark_task_retrying
            mark_task_retrying(task_id, new_retry_count, str(error), next_retry_at)
        except Exception as e2:
            logger.warning(f"[引擎] 标记重试状态失败: {e2}")

        logger.warning(
            f"[引擎] 瞬时错误，自动重试: {task_id} "
            f"(第{new_retry_count}/{max_retries}次, {delay:.0f}s后) error={error}"
        )
    else:
        # 持久错误 或 重试耗尽 → 死信队列
        if retry_count >= max_retries:
            with _lock:
                task.update(
                    status="failed",
                    progress=100,
                    stage="failed",
                    progress_text=f"重试耗尽（{max_retries}次），永久失败",
                    error=str(error),
                    dead_letter=1,
                )
            try:
                from src.storage.db import mark_task_dead
                mark_task_dead(task_id, str(error))
            except Exception:
                pass
            logger.error(f"[引擎] 重试耗尽，进入死信队列: {task_id} error={error}")
        else:
            with _lock:
                task.update(
                    status="failed",
                    progress=100,
                    stage="failed",
                    progress_text="失败",
                    error=str(error),
                )
            try:
                from src.storage.db import save_task
                save_task(task)
            except Exception:
                pass
            logger.error(f"[引擎] 持久错误，任务失败: {task_id} error={error}")


def _prepare(ctx: dict):
    src = Path(ctx["filepath"])
    dest = UPLOAD_DIR / ctx["filename"]
    if src.resolve() != dest.resolve():
        import shutil
        shutil.copy2(str(src), str(dest))
    ctx["target_path"] = str(dest)
    ctx["ext"] = src.suffix.lower()


def _cleanup_tmp(src_path: str):
    try:
        from pathlib import Path
        p = Path(src_path)
        if p.name.startswith("_tmp_") and p.exists():
            p.unlink()
            logger.info(f"[引擎] 已清理临时文件: {p.name}")
    except Exception as e:
        logger.warning(f"[引擎] 清理临时文件失败（已忽略）: {e}")


def _use_streaming(ctx: dict) -> bool:
    if RAG_STREAM_INGEST != "1":
        return False
    try:
        import fitz
        with fitz.open(ctx["target_path"]) as doc:
            pages = len(doc)
    except Exception:
        return False
    threshold = int(RAG_STREAM_MIN_PAGES)
    return pages >= threshold


def _run_sync(task_id: str, ctx: dict, skip_until: str = ""):
    """同步链路：顺序跑 sync stages，支持从断点跳过已完成的 Stage"""
    n = len(_sync_stages)
    skipping = bool(skip_until)
    for i, (name, fn) in enumerate(_sync_stages):
        # 断点续跑：跳过 checkpoint 之前已完成的 Stage
        if skipping:
            if name == skip_until:
                skipping = False
                logger.info(f"[引擎] 断点续跑: 从 {name} 开始")
            else:
                logger.info(f"[引擎] 跳过已完成 Stage: {name}")
                continue

        _emit(task_id, name, int((i + 1) / (n + 1) * 90), f"执行 {name}...")
        try:
            fn(ctx)
        except Exception as e:
            if _critical_stages.get(name):
                logger.error(f"[引擎] 关键 Stage {name} 失败，任务中止: {e}")
                raise
            logger.warning(f"[引擎] Stage {name} 失败（降级继续）: {e}")

        # P20: 每完成一个 sync Stage 保存断点
        _save_checkpoint(task_id, name, {
            "target_path": ctx.get("target_path", ""),
            "ext": ctx.get("ext", ""),
            "file_id": ctx.get("file_id"),
            "chunk_count": ctx.get("chunk_count", 0),
        })

    _finish_sync(task_id, ctx)


def _finish_sync(task_id: str, ctx: dict):
    with _lock:
        _tasks[task_id].update(
            status="done",
            progress=95,
            stage="done",
            progress_text="基础入库完成，后处理进行中...",
            chunks=ctx.get("chunk_count", 0),
            category=ctx.get("category", ""),
            file_id=ctx.get("file_id"),
            # P20: 完成后清除断点（不再需要续跑）
            checkpoint_stage="",
            checkpoint_data={},
        )
    logger.info(f"[引擎] 基础入库完成: {task_id} → {ctx.get('filename')}")

    _run_on_ingest_hook(ctx)
    _run_async_stages(task_id, ctx)

    with _lock:
        _tasks[task_id].update(status="done", progress=100, progress_text="完成", stage="done")
    try:
        from src.storage.db import save_task
        save_task(_tasks[task_id])
    except Exception:
        pass


def _run_on_ingest_hook(ctx: dict):
    payload = {
        "file_id": ctx.get("file_id"),
        "filename": ctx.get("filename"),
        "chunk_count": ctx.get("chunk_count", 0),
        "category": ctx.get("category", ""),
    }
    def _worker():
        try:
            from src.plugins.hooks import run_hook
            res = run_hook("on_ingest", payload, timeout=5.0)
            if res.get("ok"):
                logger.info(f"[引擎] on_ingest hook 已触发: {[c['plugin'] for c in res['calls'] if c.get('status')=='ok']}")
        except Exception as e:
            logger.warning(f"on_ingest hook 异常（已忽略）: {e}")
    threading.Thread(target=_worker, daemon=True).start()


def _run_streaming_pdf(task_id: str, ctx: dict):
    import os
    from .parser import parse_pdf_streaming
    from .chunker import chunk_text, clean_chunks
    from .embedder import encode
    from src.storage.db import add_file, add_chunks_batch, sync_chunk_count
    from src.storage.chroma_store import add_batch as chroma_add_batch, _use_chroma

    target = ctx["target_path"]
    force_remote_embed = False
    try:
        import fitz
        with fitz.open(target) as _d:
            _total_pages_check = len(_d)
        from config import RAG_EMBED_FORCE_REMOTE_PAGES
        force_remote_embed = _total_pages_check >= int(RAG_EMBED_FORCE_REMOTE_PAGES)
        if force_remote_embed:
            logger.info(f"大 PDF {_total_pages_check} 页 ≥ {RAG_EMBED_FORCE_REMOTE_PAGES}，强制远程 embedding")
    except Exception as e:
        logger.warning(f"判断强制远程失败（默认本地）: {e}")

    file_id = add_file(
        name=ctx["filename"], path=target, ext=".pdf",
        size=os.path.getsize(target)
    )
    ctx["file_id"] = file_id

    flush_pages = int(RAG_STREAM_FLUSH_PAGES)
    global_chunk_idx = 0
    all_batch_texts = []
    total_chunks = 0

    def on_batch(batch_text: str, done_pages: int, total_pages: int):
        nonlocal global_chunk_idx, total_chunks
        chunks = chunk_text(batch_text, source_name=ctx["filename"])
        if not chunks:
            return
        chunks = clean_chunks(chunks)
        if not chunks:
            return
        contents = [c["content"] for c in chunks]
        def _embed_progress(done, total_n):
            _emit(task_id, "streaming",
                  int(done_pages / total_pages * 90),
                  f"向量化本批 {done}/{total_n} 块（已索引 {total_chunks} 块）")
        embeddings = encode(contents, progress_cb=_embed_progress, force_remote=force_remote_embed)
        token_counts = [max(1, len(c) // 2) for c in contents]
        rows = [
            (file_id, global_chunk_idx + j, chunks[j]["content"], token_counts[j],
             embeddings[j], {"heading": chunks[j]["heading"], "source": chunks[j]["source"],
                            "markdown": bool(chunks[j].get("markdown")),
                            "section_id": chunks[j].get("section_id"),
                            "section_text": (chunks[j].get("section_text") or "")[:1000]})
            for j in range(len(chunks))
        ]
        chunk_ids = add_chunks_batch(rows)
        if _use_chroma():
            try:
                chroma_add_batch([
                    (chunk_ids[j], embeddings[j], chunks[j]["content"], file_id, global_chunk_idx + j)
                    for j in range(len(chunk_ids))
                ])
            except Exception as e:
                logger.warning(f"ChromaDB 追加失败（已忽略）: {e}")
        global_chunk_idx += len(chunks)
        total_chunks = global_chunk_idx
        pct = int(done_pages / total_pages * 90)
        _emit(task_id, "streaming", pct,
              f"流式入库 {done_pages}/{total_pages} 页（已索引 {total_chunks} 块）")
        all_batch_texts.append(batch_text)

    full_text = parse_pdf_streaming(target, on_batch, flush_pages=flush_pages)

    try:
        sync_chunk_count(file_id)
    except Exception as e:
        logger.warning(f"sync_chunk_count 失败: {e}")

    ctx["text"] = full_text or "\n\n".join(all_batch_texts)
    ctx["chunk_count"] = total_chunks

    if total_chunks == 0:
        raise ValueError("文档无有效内容")

    _emit(task_id, "classify", 92, "自动分类...")
    try:
        from .ingest_stages import _stage_classify, _stage_extract
        _stage_classify(ctx)
        _stage_extract(ctx)
    except Exception as e:
        logger.warning(f"流式尾置 Stage 失败: {e}")

    _finish_sync(task_id, ctx)


def _run_async_stages(task_id: str, ctx: dict):
    def _worker():
        try:
            for name, fn in _async_stages:
                try:
                    fn(ctx)
                except Exception as e:
                    logger.warning(f"[引擎] 异步 Stage {name} 失败（已跳过）: {e}")
            with _lock:
                _tasks[task_id].update(
                    summary=ctx.get("summary"),
                    tags=ctx.get("tags", []),
                )
        except Exception as e:
            logger.warning(f"[引擎] 异步后处理线程异常: {e}")
    threading.Thread(target=_worker, daemon=True).start()


# ============================================================
# 重试调度器（定期扫描 retrying 任务，到期后重新入队）
# ============================================================
_retry_scheduler_started = False
_retry_lock = threading.Lock()


def _start_retry_scheduler():
    """启动重试调度线程：每 10 秒扫描一次 retrying 任务，到期的重新入队执行"""
    global _retry_scheduler_started
    with _retry_lock:
        if _retry_scheduler_started:
            return
        _retry_scheduler_started = True

    def _scheduler_loop():
        while True:
            try:
                time.sleep(10)
                _process_retry_queue()
            except Exception as e:
                logger.warning(f"[引擎] 重试调度器异常（已忽略）: {e}")

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="retry-scheduler")
    t.start()
    logger.info("[引擎] 重试调度器已启动（每10s扫描）")


def _process_retry_queue():
    """扫描 retrying 任务，到期的重新入队"""
    try:
        from src.storage.db import load_retryable_tasks
        retryable = load_retryable_tasks()
    except Exception as e:
        logger.warning(f"[引擎] 加载重试队列失败: {e}")
        return

    for task in retryable:
        tid = task["task_id"]
        with _lock:
            if tid in _tasks:
                _tasks[tid].update(status="pending", progress_text="重试中...")
            else:
                _tasks[tid] = task
                _tasks[tid]["status"] = "pending"
                _tasks[tid]["progress_text"] = "重试中..."

        try:
            from src.storage.db import save_task
            save_task(_tasks[tid])
        except Exception:
            pass

        logger.info(f"[引擎] 重试任务重新入队: {tid} (第{task.get('retry_count', 0)}次)")
        t = threading.Thread(target=_run_with_limit, args=(tid,), daemon=True)
        t.start()


# ============================================================
# 内置 Stage 注册
# ============================================================
def _register_builtin_stages():
    from . import ingest_stages  # noqa: F401

_register_builtin_stages()

# P20: 启动重试调度器
_start_retry_scheduler()


# ============================================================
# 启动恢复（P20 改造：断点续跑 + 重试恢复）
# ============================================================
def recover_tasks():
    """服务启动时调用：
    1. 把上次中断的 running/pending 任务标为 pending（可恢复，非 failed）
    2. 有 checkpoint 的任务立即重新入队（断点续跑）
    3. retrying 且到期的任务重新入队（继续重试）
    """
    try:
        from src.storage.db import mark_stale_tasks_failed, load_tasks, load_resumable_tasks

        # Step 1: 把中断的 running/pending 标为 pending（非 failed）
        mark_stale_tasks_failed()

        # Step 2: 加载所有任务到内存
        for t in load_tasks():
            tid = t["task_id"]
            if tid not in _tasks:
                _tasks[tid] = t

        # Step 3: 有 checkpoint 的任务立即重新入队（断点续跑）
        resumable = load_resumable_tasks()
        resumed = 0
        for t in resumable:
            tid = t["task_id"]
            logger.info(f"[引擎] 断点续跑: {tid} → 从 {t['checkpoint_stage']} 继续")
            threading.Thread(target=_run_with_limit, args=(tid,), daemon=True).start()
            resumed += 1

        # Step 4: retrying 且到期的任务由重试调度器自动处理
        logger.info(f"[引擎] 任务恢复完成: 加载 {len(_tasks)} 个, 续跑 {resumed} 个")
    except Exception as e:
        logger.warning(f"[引擎] 任务恢复失败（已忽略）: {e}")
