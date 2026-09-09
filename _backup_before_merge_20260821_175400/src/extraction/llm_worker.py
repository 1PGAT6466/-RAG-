"""
llm_worker.py — LLM 实体抽取后台调度器（阶段 2）

职责：
  1. 入库流程同步阶段只跑规则抽取（零 LLM，保证基础图谱立即可用）
  2. LLM 抽取丢到独立后台线程异步执行，不阻塞入库队列线程
  3. 去重缓存：已做过 LLM 抽取的 chunk 跳过，避免重复烧 token
  4. 限流：控制并发的 LLM 调用，避免速率限制

对齐设计文档 §9「实体抽取成本对策」：
  批量 + 缓存 + 仅对新入库 chunk 抽取 + 抽取失败降级为规则抽取
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.storage import db
from src.extraction import entity_extractor
from config import (
    RAG_ENTITY_LLM_WORKERS, RAG_ENTITY_LLM_INTERVAL, RAG_ENTITY_LLM_MAX_CHUNKS,
)

logger = logging.getLogger("rag.extraction.llm_worker")

# 全局单例调度器
_worker: Optional["_LLMWorker"] = None
_lock = threading.Lock()


class _LLMWorker:
    """后台 LLM 抽取工作器：线程池 + 去重缓存 + 限流"""

    def __init__(self, max_workers: int = 2, min_interval: float = 0.5):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._min_interval = min_interval  # 两次 LLM 调用最短间隔（秒），限流
        self._last_call = 0.0
        self._interval_lock = threading.Lock()
        # 去重：已做过 LLM 抽取的 chunk_id 集合
        self._done_chunks: set[int] = set()
        self._pending: set[int] = set()  # 排队中的 chunk_id，防重复提交
        self._done_chunks_max = 100_000  # 去重集上限，超过后清空（避免长驻内存无限增长）

    def _throttle(self):
        """限流：确保两次调用间隔 >= min_interval"""
        with self._interval_lock:
            now = time.time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _run_one(self, chunk_id: int, content: str, file_id: int):
        """单个 chunk 的 LLM 抽取（在线程池线程内同步执行）"""
        try:
            self._throttle()
            entities = entity_extractor.extract_llm_sync(content)
            if not entities:
                logger.debug(f"chunk {chunk_id} LLM 抽取为空，跳过")
                return
            # 入库（复用 relation_builder 的单 chunk 逻辑）
            from src.extraction.relation_builder import _store_entities_and_edges
            _store_entities_and_edges(entities, chunk_id, file_id)
            logger.info(f"chunk {chunk_id} LLM 抽取完成: {len(entities)} 实体")
        except Exception as e:
            logger.warning(f"chunk {chunk_id} LLM 抽取失败（规则已兜底）: {e}")
        finally:
            with _lock:
                self._done_chunks.add(chunk_id)
                self._pending.discard(chunk_id)
                if len(self._done_chunks) > self._done_chunks_max:
                    self._done_chunks.clear()
                    logger.info(f"[llm_worker] 去重集已达上限，清空（降低内存占用，牺牲少量重复抽取）")

    def submit_file(self, file_id: int):
        """提交一个文件的全部 chunk 做后台 LLM 抽取（去重 + 非阻塞 + 可选 max_chunks 限流）"""
        chunks = db.get_chunks_by_file(file_id)
        # 成本保护：LLM 抽取慢（推理型 ~100s/chunk），默认限制每文件抽取 chunk 数，避免大文档无限烧 token
        max_chunks = int(RAG_ENTITY_LLM_MAX_CHUNKS)
        submitted = 0
        for c in chunks:
            if max_chunks > 0 and submitted >= max_chunks:
                logger.info(f"文件 {file_id} 已达 LLM 抽取上限 {max_chunks} chunk，剩余 {len(chunks) - submitted} chunk 跳过（仅规则抽取）")
                break
            cid = c["id"]
            with _lock:
                if cid in self._done_chunks or cid in self._pending:
                    continue  # 已抽取或已排队
                self._pending.add(cid)
            # 提交到线程池
            try:
                self._executor.submit(self._run_one, cid, c["content"], file_id)
                submitted += 1
            except Exception as e:
                logger.warning(f"提交 chunk {cid} LLM 抽取失败: {e}")
                with _lock:
                    self._pending.discard(cid)
        logger.info(f"文件 {file_id} 已提交 {submitted}/{len(chunks)} chunk 到 LLM 抽取队列")

    def shutdown(self):
        self._executor.shutdown(wait=False)


def _get_worker() -> _LLMWorker:
    global _worker
    with _lock:
        if _worker is None:
            max_workers = int(RAG_ENTITY_LLM_WORKERS)
            interval = float(RAG_ENTITY_LLM_INTERVAL)
            _worker = _LLMWorker(max_workers=max_workers, min_interval=interval)
        return _worker


def submit_file(file_id: int):
    """对外入口：提交文件做后台 LLM 抽取"""
    _get_worker().submit_file(file_id)


def get_status() -> dict:
    """返回 LLM 抽取进度（供前端/调试）"""
    w = _get_worker()
    with _lock:
        return {
            "done_chunks": len(w._done_chunks),
            "pending_chunks": len(w._pending),
            "max_workers": w._executor._max_workers,
        }


def shutdown():
    global _worker
    with _lock:
        if _worker is not None:
            _worker.shutdown()
            _worker = None
