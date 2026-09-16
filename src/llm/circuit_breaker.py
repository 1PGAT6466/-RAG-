"""LLM provider 级熔断器 + 限流（Phase 3）

设计：
- 每个 provider（deepseek/siliconflow/mimo）独立统计
- 连续 N 次失败触发熔断（短时间跳过该 provider）
- 令牌桶限流（每秒最多 N 次请求）
- 熔断恢复后半开探测（放一个请求试探）

用法：
  from src.llm.circuit_breaker import get_breaker
  breaker = get_breaker("deepseek")
  if breaker.is_open():
      # 跳过该 provider
      pass
  try:
      result = call_api(...)
      breaker.record_success()
  except Exception:
      breaker.record_failure()
"""
import time
import threading
import logging

logger = logging.getLogger("rag.llm.breaker")

# 熔断器配置
FAILURE_THRESHOLD = 5       # 连续失败 N 次触发熔断
RECOVERY_TIMEOUT = 60       # 熔断恢复超时（秒）
HALF_OPEN_REQUESTS = 1      # 半开状态允许的试探请求数
RATE_LIMIT_PER_SEC = 10     # 每秒最大请求数


class CircuitBreaker:
    """单个 provider 的熔断器"""

    def __init__(self, name: str):
        self.name = name
        self._failures = 0
        self._last_failure = 0.0
        self._state = "closed"  # closed / open / half_open
        self._half_open_calls = 0
        self._lock = threading.Lock()

        # 令牌桶限流
        self._rate_limit = RATE_LIMIT_PER_SEC
        self._tokens = float(RATE_LIMIT_PER_SEC)
        self._last_refill = time.monotonic()

    def is_open(self) -> bool:
        """检查熔断器是否打开（跳过该 provider）"""
        with self._lock:
            if self._state == "closed":
                return False
            if self._state == "open":
                # 检查是否超过恢复超时
                if time.monotonic() - self._last_failure > RECOVERY_TIMEOUT:
                    self._state = "half_open"
                    self._half_open_calls = 0
                    logger.info(f"[{self.name}] 熔断恢复 → half_open")
                    return False
                return True
            # half_open: 允许少量试探
            if self._half_open_calls >= HALF_OPEN_REQUESTS:
                return True
            return False

    def acquire_token(self) -> bool:
        """令牌桶限流：获取一个请求令牌"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate_limit, self._tokens + elapsed * self._rate_limit)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                if self._state == "half_open":
                    self._half_open_calls += 1
                return True
            return False

    def record_success(self):
        """记录成功（重置熔断状态）"""
        with self._lock:
            self._failures = 0
            if self._state != "closed":
                logger.info(f"[{self.name}] 熔断恢复 → closed")
            self._state = "closed"
            self._half_open_calls = 0

    def record_failure(self):
        """记录失败（可能触发熔断）"""
        with self._lock:
            self._failures += 1
            self._last_failure = time.monotonic()
            if self._failures >= FAILURE_THRESHOLD:
                if self._state != "open":
                    logger.warning(f"[{self.name}] 连续 {self._failures} 次失败 → 熔断")
                self._state = "open"

    def get_state(self) -> dict:
        """获取熔断器状态（供诊断）"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state,
                "failures": self._failures,
                "tokens": round(self._tokens, 1),
            }


# 全局熔断器实例
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(provider: str) -> CircuitBreaker:
    """获取指定 provider 的熔断器"""
    if provider not in _breakers:
        _breakers[provider] = CircuitBreaker(provider)
    return _breakers[provider]


def get_all_states() -> list[dict]:
    """获取所有熔断器状态（供 API 诊断）"""
    return [b.get_state() for b in _breakers.values()]
