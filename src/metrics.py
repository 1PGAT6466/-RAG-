"""
metrics.py — 轻量指标收集器（P25）
==================================

设计决策：
  - 不引 Prometheus/Grafana（单机内网场景不需要）
  - 进程内滑动窗口计数器（60s 窗口，10s 粒度）
  - /api/metrics 端点输出 JSON 格式（可被巡检器/脚本消费）
  - 指标：QPS、延迟直方图、LLM 降级率、任务失败率、缓存命中率

用法：
  from src.metrics import record_request, record_llm_call, record_task, record_cache
  record_request(path="/api/search", status=200, latency_ms=350)
  record_llm_call(provider="deepseek", success=True, tokens_in=100, tokens_out=50)
  record_task(status="done")
  record_cache(hit=True)
"""
import collections
import logging
import threading
import time

logger = logging.getLogger("rag.metrics")

# 滑动窗口参数
_WINDOW_SIZE = 60       # 窗口大小（秒）
_BUCKET_SIZE = 10       # 桶粒度（秒）
_NUM_BUCKETS = _WINDOW_SIZE // _BUCKET_SIZE  # 6 个桶

_lock = threading.Lock()


class _SlidingWindow:
    """滑动窗口计数器（线程安全）"""

    def __init__(self):
        self._buckets = [0] * _NUM_BUCKETS
        self._last_update = time.monotonic()

    def _rotate(self):
        now = time.monotonic()
        elapsed = now - self._last_update
        if elapsed >= _BUCKET_SIZE:
            steps = min(int(elapsed / _BUCKET_SIZE), _NUM_BUCKETS)
            for _ in range(steps):
                self._buckets.pop(0)
                self._buckets.append(0)
            self._last_update = now

    def increment(self, n: int = 1):
        with _lock:
            self._rotate()
            self._buckets[-1] += n

    def total(self) -> int:
        with _lock:
            self._rotate()
            return sum(self._buckets)


class _LatencyHistogram:
    """简易延迟直方图（线程安全）"""

    def __init__(self, buckets_ms: list[int] = None):
        self._buckets_ms = buckets_ms or [50, 100, 200, 500, 1000, 2000, 5000, 10000]
        self._counts = [0] * (len(self._buckets_ms) + 1)  # +1 for +Inf
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, value_ms: float):
        with self._lock:
            self._sum += value_ms
            self._count += 1
            for i, bound in enumerate(self._buckets_ms):
                if value_ms <= bound:
                    self._counts[i] += 1
                    return
            self._counts[-1] += 1  # +Inf

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "buckets_ms": self._buckets_ms + ["+Inf"],
                "counts": list(self._counts),
                "sum_ms": round(self._sum, 1),
                "count": self._count,
                "avg_ms": round(self._sum / self._count, 1) if self._count else 0,
                "p50_ms": self._percentile(50),
                "p95_ms": self._percentile(95),
                "p99_ms": self._percentile(99),
            }

    def _percentile(self, p: int) -> int:
        """近似百分位（基于桶边界）"""
        if self._count == 0:
            return 0
        target = self._count * p / 100
        cumulative = 0
        for i, count in enumerate(self._counts):
            cumulative += count
            if cumulative >= target:
                return self._buckets_ms[i] if i < len(self._buckets_ms) else 99999
        return 99999


# ============================================================
# 全局指标实例
# ============================================================
# 请求计数（按路径）
_request_counts: dict[str, _SlidingWindow] = collections.defaultdict(_SlidingWindow)
_request_errors: dict[str, _SlidingWindow] = collections.defaultdict(_SlidingWindow)
_request_latency = _LatencyHistogram()

# LLM 调用
_llm_calls = _SlidingWindow()
_llm_failures = _SlidingWindow()
_llm_degrades = _SlidingWindow()
_llm_tokens_in = 0
_llm_tokens_out = 0
_llm_lock = threading.Lock()

# 任务
_task_total = _SlidingWindow()
_task_success = _SlidingWindow()
_task_failure = _SlidingWindow()
_task_retry = _SlidingWindow()

# 缓存
_cache_hits = _SlidingWindow()
_cache_misses = _SlidingWindow()


# ============================================================
# 记录函数（由各模块调用）
# ============================================================
def record_request(path: str, status: int, latency_ms: float):
    """记录 HTTP 请求"""
    _request_counts[path].increment()
    _request_latency.observe(latency_ms)
    if status >= 400:
        _request_errors[path].increment()


def record_llm_call(provider: str, success: bool, tokens_in: int = 0, tokens_out: int = 0, degraded: bool = False):
    """记录 LLM 调用"""
    _llm_calls.increment()
    if not success:
        _llm_failures.increment()
    if degraded:
        _llm_degrades.increment()
    with _llm_lock:
        global _llm_tokens_in, _llm_tokens_out
        _llm_tokens_in += tokens_in
        _llm_tokens_out += tokens_out


def record_task(status: str):
    """记录任务状态变更"""
    _task_total.increment()
    if status == "done":
        _task_success.increment()
    elif status == "failed":
        _task_failure.increment()
    elif status == "retrying":
        _task_retry.increment()


def record_cache(hit: bool):
    """记录缓存命中/未命中"""
    if hit:
        _cache_hits.increment()
    else:
        _cache_misses.increment()


# ============================================================
# 查询函数
# ============================================================
def get_metrics() -> dict:
    """获取全部指标快照（供 /api/metrics 端点）"""
    total_requests = sum(sw.total() for sw in _request_counts.values())
    total_errors = sum(sw.total() for sw in _request_errors.values())

    llm_total = _llm_calls.total()
    llm_fails = _llm_failures.total()
    llm_degs = _llm_degrades.total()

    task_total = _task_total.total()
    task_ok = _task_success.total()
    task_fail = _task_failure.total()
    task_retry = _task_retry.total()

    cache_hit = _cache_hits.total()
    cache_miss = _cache_misses.total()
    cache_total = cache_hit + cache_miss

    return {
        "timestamp": time.time(),
        "window_seconds": _WINDOW_SIZE,
        "http": {
            "requests_total": total_requests,
            "errors_total": total_errors,
            "error_rate": round(total_errors / max(total_requests, 1), 4),
            "latency": _request_latency.snapshot(),
        },
        "llm": {
            "calls": llm_total,
            "failures": llm_fails,
            "failure_rate": round(llm_fails / max(llm_total, 1), 4),
            "degrades": llm_degs,
            "degrade_rate": round(llm_degs / max(llm_total, 1), 4),
            "tokens_in": _llm_tokens_in,
            "tokens_out": _llm_tokens_out,
        },
        "tasks": {
            "total": task_total,
            "success": task_ok,
            "failure": task_fail,
            "retry": task_retry,
            "failure_rate": round(task_fail / max(task_total, 1), 4),
        },
        "cache": {
            "hits": cache_hit,
            "misses": cache_miss,
            "hit_rate": round(cache_hit / max(cache_total, 1), 4),
        },
    }
