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

任务状态机：
  pending → running → done/failed/cancelled
  任务状态驻留内存（_tasks dict），重启后通过 recover_tasks 从 DB 恢复

大规模文档路径：
  PDF ≥ 20 页走流式（parse_pdf_streaming），逐页解析+增量 embed，避免 OOM
  chunk 数 > EMBED_REMOTE_THRESHOLD(300) 自动切远程 embedding API

触发器（trigger）只是"把文件喂给引擎"的入口，不各自实现逻辑：
  - HTTP 上传 → enqueue()
  - 文件夹监控 → watch_folder.py 轮询到新文件 → enqueue()
  - cron → cron 任务调用 enqueue()
"""
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from config import UPLOAD_DIR, RAG_STREAM_INGEST, RAG_STREAM_MIN_PAGES, RAG_STREAM_FLUSH_PAGES, RAG_INGEST_MAX_CONCURRENT

logger = logging.getLogger("rag.engine")

# ============================================================
# 任务状态机
# ============================================================
# pending -> running -> done | failed
# running 内部有 detail 字段描绘当前 Stage
_tasks: dict[str, dict] = {}
_lock = threading.Lock()

# 入库并发限流：信号量上限=RAG_INGEST_MAX_CONCURRENT。
# enqueue 仍立即返回 task_id（状态 pending），但 worker 线程启动后先 acquire 信号量
# 才真正执行 _run，超限的任务线程阻塞排队，避免批量上传时线程爆炸 OOM。
_ingest_semaphore = threading.BoundedSemaphore(max(1, RAG_INGEST_MAX_CONCURRENT))

# 任务状态驻留内存上限：达到后淘汰最旧的已完成任务，避免长时间运行内存无限增长
_MAX_TASKS = 1000


def _evict_old_tasks():
    """淘汰最旧的已完成任务（done/failed），保留 running/pending，控制内存"""
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


# ============================================================
# Stage 注册表
# ============================================================
# 每个 Stage 是 (name, fn)，fn 签名：fn(ctx: dict) -> dict（返回本阶段产物，写入 ctx）
# ctx 会在所有 Stage 间传递，携带 file_id / chunks / text / 等中间产物
_sync_stages: list[tuple[str, Callable]] = []
_async_stages: list[tuple[str, Callable]] = []


# 每个 Stage 的元数据：name -> 是否 critical（失败则整任务失败，而非降级跳过）
_critical_stages: dict[str, bool] = {}


def register_stage(name: str, stage_type: str = "sync", critical: bool = False):
    """注册 Stage 的装饰器。stage_type ∈ {'sync','async'}

    用法：
        @register_stage("embed", stage_type="sync", critical=True)
        def _stage_embed(ctx): ...

    sync  ：入库必跑、快速、失败降级
    async ：后处理增强，后台线程执行
    critical：True 则该 Stage 失败会导致整任务失败（如向量化/入库这类不可缺的）
    """
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
        }
        _evict_old_tasks()
    # 持久化初始状态
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
    """并发限流包装：先 acquire 信号量再执行 _run，超限任务阻塞排队。

    保持 enqueue 立即返回、任务状态 pending 的契约；真正执行时才占并发位。
    信号量释放放在 finally，异常/正常/清理路径都保证归还。
    """
    _ingest_semaphore.acquire()
    try:
        _run(task_id)
    finally:
        _ingest_semaphore.release()


def get_status(task_id: str) -> Optional[dict]:
    with _lock:
        t = _tasks.get(task_id)
    return dict(t) if t else None


def list_tasks() -> list[dict]:
    with _lock:
        return [dict(v) for v in _tasks.values()]


def _run(task_id: str):
    """引擎主循环：按顺序跑 sync stages，再异步跑 async stages"""
    with _lock:
        t = _tasks[task_id]
    ctx = {
        "task_id": task_id,
        "filepath": t["filepath"],
        "filename": t["filename"],
        "emit": lambda stage, progress, text: _emit(task_id, stage, progress, text),
    }

    try:
        _emit(task_id, "prepare", 2, "准备文件...")
        _prepare(ctx)

        # 流式路径：大 PDF（需逐页解析/OCR）走边解析边入库，尽快可检索
        if ctx["ext"] == ".pdf" and _use_streaming(ctx):
            _run_streaming_pdf(task_id, ctx)
        else:
            _run_sync(task_id, ctx)
    except Exception as e:
        import traceback
        logger.error(f"[引擎] 任务失败 {task_id}: {e}\n{traceback.format_exc()}")
        with _lock:
            _tasks[task_id].update(status="failed", progress=100, stage="failed", progress_text="失败", error=str(e))
        # 持久化失败状态
        try:
            from src.storage.db import save_task
            save_task(_tasks[task_id])
        except Exception:
            pass
    finally:
        # 清理上传临时文件（_tmp_ 前缀），避免大文件上传后残留占用磁盘
        _cleanup_tmp(ctx["filepath"])


def _prepare(ctx: dict):
    """准备阶段：复制文件到 uploads"""
    src = Path(ctx["filepath"])
    dest = UPLOAD_DIR / ctx["filename"]
    if src.resolve() != dest.resolve():
        import shutil
        shutil.copy2(str(src), str(dest))
    ctx["target_path"] = str(dest)
    ctx["ext"] = src.suffix.lower()


def _cleanup_tmp(src_path: str):
    """清理上传临时文件（_tmp_ 前缀），避免大文件上传后残留"""
    try:
        from pathlib import Path
        p = Path(src_path)
        if p.name.startswith("_tmp_") and p.exists():
            p.unlink()
            logger.info(f"[引擎] 已清理临时文件: {p.name}")
    except Exception as e:
        logger.warning(f"[引擎] 清理临时文件失败（已忽略）: {e}")


def _use_streaming(ctx: dict) -> bool:
    """是否走流式 PDF 路径。默认开启（RAG_STREAM_INGEST=1），小文件可关。"""
    if RAG_STREAM_INGEST != "1":
        return False
    # 小 PDF（页数阈值内）不值得流式，整本解析更快
    try:
        import fitz
        with fitz.open(ctx["target_path"]) as doc:
            pages = len(doc)
    except Exception:
        return False
    threshold = int(RAG_STREAM_MIN_PAGES)
    return pages >= threshold


def _run_sync(task_id: str, ctx: dict):
    """原同步链路：顺序跑 sync stages，再异步跑 async stages"""
    # 顺序执行 sync stages
    n = len(_sync_stages)
    for i, (name, fn) in enumerate(_sync_stages):
        _emit(task_id, name, int((i + 1) / (n + 1) * 90), f"执行 {name}...")
        try:
            fn(ctx)
        except Exception as e:
            if _critical_stages.get(name):
                logger.error(f"[引擎] 关键 Stage {name} 失败，任务中止: {e}")
                raise
            logger.warning(f"[引擎] Stage {name} 失败（降级继续）: {e}")

    _finish_sync(task_id, ctx)


def _finish_sync(task_id: str, ctx: dict):
    """汇总基础结果 + 异步后处理 + on_ingest hook + 置完成"""
    with _lock:
        _tasks[task_id].update(
            status="done",
            progress=95,
            stage="done",
            progress_text="基础入库完成，后处理进行中...",
            chunks=ctx.get("chunk_count", 0),
            category=ctx.get("category", ""),
            file_id=ctx.get("file_id"),
        )
    logger.info(f"[引擎] 基础入库完成: {task_id} → {ctx.get('filename')}")

    # on_ingest hook（插件挂载点，后台线程容错执行，不阻塞主链路）
    _run_on_ingest_hook(ctx)

    # 异步后处理（摘要/标签/预索引），不阻塞状态置 done
    _run_async_stages(task_id, ctx)

    with _lock:
        _tasks[task_id].update(status="done", progress=100, progress_text="完成", stage="done")
    # 持久化最终状态
    try:
        from src.storage.db import save_task
        save_task(_tasks[task_id])
    except Exception:
        pass


def _run_on_ingest_hook(ctx: dict):
    """触发 on_ingest 插件事件（后台线程，容错，不阻塞）"""
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
    """流式 PDF 入库：边解析边分块→向量化→追加入库。

    目标：丢进去后几分钟内即可检索到已识别的部分（首个小 batch 可搜），
    而不是等整本解析完才能搜。
    """
    import os
    from .parser import parse_pdf_streaming
    from .chunker import chunk_text, clean_chunks
    from .embedder import encode
    from src.storage.db import add_file, add_chunks_batch, sync_chunk_count
    from src.storage.chroma_store import add_batch as chroma_add_batch, _use_chroma

    target = ctx["target_path"]

    # 判断是否强制远程 embedding：大 PDF（页数超阈值）本地 CPU embedding 太慢，
    # 逐批 embed 时直接走 SiliconFlow 远程（代价：少量 API 费用，换取分钟级完成）
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

    # 先建文件记录（这样文件列表里立刻可见，状态可追踪）
    file_id = add_file(
        name=ctx["filename"], path=target, ext=".pdf",
        size=os.path.getsize(target)
    )
    ctx["file_id"] = file_id

    flush_pages = int(RAG_STREAM_FLUSH_PAGES)
    global_chunk_idx = 0
    all_batch_texts = []  # 收集整本（供摘要/分类用）
    total_chunks = 0

    def on_batch(batch_text: str, done_pages: int, total_pages: int):
        nonlocal global_chunk_idx, total_chunks
        # 分块 + 清洗（与同步链路的 chunk→clean 两段解耦保持一致）
        chunks = chunk_text(batch_text, source_name=ctx["filename"])
        if not chunks:
            return
        chunks = clean_chunks(chunks)
        if not chunks:
            return
        # 重新编号（全局连续）
        contents = [c["content"] for c in chunks]
        # 向量化（带进度回调：大 batch 嵌入时逐批上报，避免长时间无反馈）
        def _embed_progress(done, total_n):
            _emit(task_id, "streaming",
                  int(done_pages / total_pages * 90),
                  f"向量化本批 {done}/{total_n} 块（已索引 {total_chunks} 块）")
        embeddings = encode(contents, progress_cb=_embed_progress, force_remote=force_remote_embed)
        token_counts = [max(1, len(c) // 2) for c in contents]
        rows = [
            (file_id, global_chunk_idx + j, chunks[j]["content"], token_counts[j],
             embeddings[j], {"heading": chunks[j]["heading"], "source": chunks[j]["source"],
                            "markdown": bool(chunks[j].get("markdown"))})
            for j in range(len(chunks))
        ]
        chunk_ids = add_chunks_batch(rows)
        # ChromaDB 追加
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

    # 流式解析（阻塞直到整本完成，但每批都实时入库）
    full_text = parse_pdf_streaming(target, on_batch, flush_pages=flush_pages)

    # 同步 chunk_count
    try:
        sync_chunk_count(file_id)
    except Exception as e:
        logger.warning(f"sync_chunk_count 失败: {e}")

    ctx["text"] = full_text or "\n\n".join(all_batch_texts)
    ctx["chunk_count"] = total_chunks

    if total_chunks == 0:
        raise ValueError("文档无有效内容")

    # classify + extract：复用已有 Stage 逻辑（它们只依赖 ctx/file_id）
    _emit(task_id, "classify", 92, "自动分类...")
    try:
        from .ingest_stages import _stage_classify, _stage_extract
        _stage_classify(ctx)
        _stage_extract(ctx)
    except Exception as e:
        logger.warning(f"流式尾置 Stage 失败: {e}")

    _finish_sync(task_id, ctx)


def _run_async_stages(task_id: str, ctx: dict):
    """后台跑 async stages，完成后更新 task 的 summary/tags 等字段"""
    def _worker():
        try:
            for name, fn in _async_stages:
                try:
                    fn(ctx)
                except Exception as e:
                    logger.warning(f"[引擎] 异步 Stage {name} 失败（已跳过）: {e}")
            # 回写后处理结果到任务状态
            with _lock:
                _tasks[task_id].update(
                    summary=ctx.get("summary"),
                    tags=ctx.get("tags", []),
                )
        except Exception as e:
            logger.warning(f"[引擎] 异步后处理线程异常: {e}")

    threading.Thread(target=_worker, daemon=True).start()


# ============================================================
# 内置 Stage 注册（实际实现放各自模块，这里只做挂载）
# ============================================================
def _register_builtin_stages():
    """挂载内置 Stage（延迟 import，避免循环依赖）"""
    from . import ingest_stages  # noqa: F401


_register_builtin_stages()


# ============================================================
# 启动恢复（从 DB 加载任务状态，标记中断任务）
# ============================================================
def recover_tasks():
    """服务启动时调用：把上次中断的 running/pending 任务标记为 failed，加载历史任务到内存"""
    try:
        from src.storage.db import mark_stale_tasks_failed, load_tasks
        mark_stale_tasks_failed()
        for t in load_tasks():
            tid = t["task_id"]
            if tid not in _tasks:
                _tasks[tid] = t
        logger.info(f"[引擎] 任务恢复完成，加载 {len(_tasks)} 个历史任务")
    except Exception as e:
        logger.warning(f"[引擎] 任务恢复失败（已忽略）: {e}")
