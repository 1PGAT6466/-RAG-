"""
后台任务队列 — 异步文档处理
============================
上传立即返回，后台解析+分块+向量化，前端轮询进度
"""
import logging
import threading
import time
import os
from pathlib import Path
from typing import Optional
from config import UPLOAD_DIR

logger = logging.getLogger("rag.task_queue")

# 任务状态: pending | parsing | chunking | embedding | done | failed
_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def enqueue(filepath: str, filename: str) -> str:
    """加入队列，返回 task_id"""
    import uuid
    task_id = str(uuid.uuid4())[:8]
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "filename": filename,
            "filepath": filepath,
            "status": "pending",
            "progress": 0,
            "progress_text": "等待处理...",
            "total_pages": 0,
            "chunks": 0,
            "category": "",
            "file_id": None,
            "error": None,
            "created_at": time.time(),
        }
    # 启动后台线程
    t = threading.Thread(target=_process, args=(task_id,), daemon=True)
    t.start()
    logger.info(f"任务加入队列: {task_id} → {filename}")
    return task_id


def get_status(task_id: str) -> Optional[dict]:
    with _lock:
        t = _tasks.get(task_id)
    if not t:
        return None
    # 返回副本，避免外部修改
    return dict(t)


def _process(task_id: str):
    """后台处理线程"""
    with _lock:
        t = _tasks[task_id]
    filepath = t["filepath"]
    filename = t["filename"]

    try:
        # 1. 复制到 uploads
        _set_status(task_id, "parsing", 5, "复制文件...")
        src = Path(filepath)
        dest = UPLOAD_DIR / filename
        if src.resolve() != dest.resolve():
            import shutil
            shutil.copy2(filepath, dest)
        target_path = str(dest)

        # 2. 解析（pdfplumber，大文件逐页更新进度）
        from .parser import _parse_pdf_with_progress
        ext = src.suffix.lower()
        if ext == ".pdf":
            text = _parse_pdf_with_progress(target_path, task_id, _set_status)
        else:
            from .parser import parse_file
            _set_status(task_id, "parsing", 30, f"解析 {ext} 文件...")
            text = parse_file(target_path)

        if not text or len(text.strip()) < 50:
            raise ValueError("文档无有效内容")

        # 3. 分块
        _set_status(task_id, "chunking", 70, "分块处理...")
        from .chunker import chunk_text
        chunks = chunk_text(text, source_name=filename)
        if not chunks:
            raise ValueError("分块后无有效内容")

        # 4. 向量化
        _set_status(task_id, "embedding", 80, f"向量化 ({len(chunks)} 块)...")
        from .embedder import encode
        contents = [c["content"] for c in chunks]
        embeddings = encode(contents)
        token_counts = [max(1, len(c) // 2) for c in contents]

        # 5. 入库
        _set_status(task_id, "embedding", 90, "写入数据库...")
        from src.storage.db import add_file, add_chunks_batch, update_file_category
        file_id = add_file(
            name=filename, path=str(dest), ext=ext,
            size=dest.stat().st_size
        )
        batch = [
            (file_id, c["index"], c["content"], token_counts[i], embeddings[i],
             {"heading": c["heading"], "source": c["source"]})
            for i, c in enumerate(chunks)
        ]
        chunk_ids = add_chunks_batch(batch)
        from src.storage.db import sync_chunk_count
        sync_chunk_count(file_id)

        # ChromaDB 向量写入（Feature Flag 控制）
        try:
            from src.storage.chroma_store import add_batch, _use_chroma
            if _use_chroma():
                add_batch([
                    (chunk_ids[i], embeddings[i], contents[i], file_id, i)
                    for i in range(len(chunk_ids))
                ])
        except Exception as e:
            logger.warning(f"ChromaDB 写入失败（已忽略）: {e}")

        # 自动分类
        from .ingest import _auto_classify
        cat = _auto_classify(filename, text)
        if cat != "未分类":
            update_file_category(file_id, cat)

        # 实体抽取 + 图谱入库（Feature Flag 控制，失败不阻断主链路）
        try:
            import os
            if os.getenv("RAG_ENTITY_EXTRACT", "1") == "1":
                from src.extraction.relation_builder import process_file, process_file_rule
                use_llm = os.getenv("RAG_ENTITY_LLM", "1") == "1"
                if use_llm:
                    # 同步快速跑规则抽取（零 LLM，基础图谱立即可用），
                    # LLM 抽取丢到独立后台线程异步执行（不阻塞队列线程，避免大文件上千次 LLM 调用卡死队列）
                    try:
                        process_file_rule(file_id)
                    except Exception as e:
                        logger.warning(f"规则实体抽取失败（已忽略）: {e}")
                    from src.extraction.llm_worker import submit_file
                    submit_file(file_id)
                else:
                    process_file_rule(file_id)
        except Exception as e:
            logger.warning(f"实体抽取失败（已忽略）: {e}")

        # Workflow hook：入库后处理（声明式插件，容错不阻断）
        try:
            from src.plugins.hooks import run_hook
            run_hook("on_ingest", {
                "file_id": file_id,
                "filename": filename,
                "chunks": chunks,
                "category": cat,
            })
        except Exception as e:
            logger.warning(f"on_ingest hook 异常（已忽略）: {e}")

        with _lock:
            _tasks[task_id].update(
                status="done", progress=100,
                progress_text=f"完成：{len(chunks)} 块",
                chunks=len(chunks), category=cat,
                file_id=file_id,
            )
        logger.info(f"任务完成: {task_id} → {filename} ({len(chunks)} chunks)")

    except Exception as e:
        import traceback
        logger.error(f"任务失败: {task_id} → {filename}: {e}\n{traceback.format_exc()}")
        with _lock:
            _tasks[task_id].update(
                status="failed", progress=100,
                progress_text="处理失败",
                error=str(e),
            )

    finally:
        # 清理临时文件（如果不在 uploads 内）
        try:
            tmp = Path(filepath)
            if tmp.exists() and tmp.parent != UPLOAD_DIR:
                os.remove(tmp)
        except Exception:
            pass


def _set_status(task_id: str, status: str, progress: int, text: str):
    with _lock:
        t = _tasks.get(task_id)
        if t:
            t.update(status=status, progress=progress, progress_text=text)
