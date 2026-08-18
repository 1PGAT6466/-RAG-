"""
限流 — 内存固定窗口限流器（认证端点防暴力破解）
================================================
平台定位：登录/注册等敏感端点需要限流，防止暴力破解。
单进程内存实现，足够当前单实例部署；多实例时替换为 Redis。
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """返回 True 表示放行，False 表示超出限流。"""
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            # 清理窗口外的旧记录
            while dq and dq[0] <= now - self.window_seconds:
                dq.popleft()
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            return True

    def remaining(self, key: str) -> int:
        with self._lock:
            dq = self._hits[key]
            now = time.time()
            while dq and dq[0] <= now - self.window_seconds:
                dq.popleft()
            return max(0, self.max_requests - len(dq))


# 登录/注册各用一个限流器（独立计数，避免相互干扰）
login_limiter = RateLimiter(max_requests=10, window_seconds=60)
register_limiter = RateLimiter(max_requests=5, window_seconds=60)
