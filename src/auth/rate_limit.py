"""
限流 — 内存固定窗口限流器（认证端点防暴力破解）
================================================
平台定位：登录/注册等敏感端点需要限流，防止暴力破解。
单进程内存实现，足够当前单实例部署；多实例时替换为 Redis。
"""
import threading
import time
from collections import defaultdict, deque, OrderedDict


class RateLimiter:
    _MAX_KEYS = 10000  # 内存上限：最多跟踪 10000 个不同 key

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = {}
        self._access_order: OrderedDict[str, None] = OrderedDict()  # C4: OrderedDict O(1)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """返回 True 表示放行，False 表示超出限流。"""
        now = time.time()
        with self._lock:
            self._evict_stale(now)
            if key not in self._hits and len(self._hits) >= self._MAX_KEYS:
                self._evict_lru()
            dq = self._hits.setdefault(key, deque())
            self._touch(key)
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            return True

    def remaining(self, key: str) -> int:
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return self.max_requests
            now = time.time()
            while dq and dq[0] <= now - self.window_seconds:
                dq.popleft()
            return max(0, self.max_requests - len(dq))

    def _evict_stale(self, now: float):
        """清理所有 key 的过期记录，并删除空 key"""
        empty_keys = []
        for key, dq in self._hits.items():
            while dq and dq[0] <= now - self.window_seconds:
                dq.popleft()
            if not dq:
                empty_keys.append(key)
        for key in empty_keys:
            del self._hits[key]
            self._access_order.pop(key, None)  # C4: O(1)

    def _evict_lru(self):
        """淘汰最久未访问的 key"""
        while self._access_order and len(self._hits) >= self._MAX_KEYS:
            oldest = next(iter(self._access_order))
            del self._hits[oldest]
            del self._access_order[oldest]

    def _touch(self, key: str):
        """更新 LRU 访问顺序"""
        self._access_order.pop(key, None)
        self._access_order[key] = None  # OrderedDict 末尾 = 最近访问


# 登录/注册各用一个限流器（独立计数，避免相互干扰）
login_limiter = RateLimiter(max_requests=5, window_seconds=60)
register_limiter = RateLimiter(max_requests=5, window_seconds=60)
