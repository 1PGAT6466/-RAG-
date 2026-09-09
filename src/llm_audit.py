"""
llm_audit.py — LLM 调用审计（成本 + 质量可观测，轻量无外部依赖）

用途：记录每次 LLM 调用的环节(scope)、provider、输入/输出 token 估算、
耗时、是否降级/重试/失败，供成本度量、质量回溯、降级率统计。

设计：进程内计数（无 DB 依赖，避免每次 LLM 调用引入持久化开销），
配合结构化日志输出。可选 get_stats() 暴露聚合统计供 /api/health 或诊断。

统计维度（全局累加，进程生命周期内有效）：
  - calls: 每次 provider 的调用次数
  - failures: 失败次数（触发降级到下一个 provider）
  - retries: 重试次数
  - total_tokens_in / total_tokens_out: 累计 token（估算）
  - total_ms: 累计耗时
"""
import logging
import threading
import time

logger = logging.getLogger("rag.llm.audit")

_lock = threading.Lock()
_stats: dict = {
    "calls": {},          # provider -> count
    "failures": {},       # provider -> count
    "retries": 0,
    "empty_content": 0,   # 返回空 content 次数
    "total_tokens_in": 0,
    "total_tokens_out": 0,
    "total_ms": 0,
    # 业务级降级计数（非 LLM 异常，而是调用方因超时/跑偏/静默回退主动降级）
    "degrades": {},       # scope -> count（如 intent_llm_timeout / rewrite_drift / rewrite_error）
}


def _estimate_tokens(messages: list[dict]) -> int:
    """粗略估算输入 token（约 1 token ≈ 4 字节英文 / 2 字符中文），仅用于维度统计。"""
    n = 0
    for m in messages or []:
        c = m.get("content", "") or ""
        n += len(c) // 2 + 1
    return max(1, n)


def record_call(scope: str, provider: str, messages: list[dict],
                out_text: str = "", elapsed_ms: float = 0.0):
    """记录一次成功的 LLM 调用"""
    with _lock:
        _stats["calls"][provider] = _stats["calls"].get(provider, 0) + 1
        _stats["total_tokens_in"] += _estimate_tokens(messages)
        _stats["total_tokens_out"] += max(1, len(out_text or "") // 2)
        _stats["total_ms"] += elapsed_ms
    logger.info(f"[LLM审计] scope={scope} provider={provider} "
                f"out={len(out_text or '')}chars t={elapsed_ms:.0f}ms")


def record_failure(provider: str, reason: str = ""):
    """记录一次 provider 失败（触发降级）"""
    with _lock:
        _stats["failures"][provider] = _stats["failures"].get(provider, 0) + 1
    logger.warning(f"[LLM审计] provider={provider} 失败(降级) reason={reason[:80]}")


def record_retry(provider: str):
    with _lock:
        _stats["retries"] += 1


def record_empty_content(provider: str):
    with _lock:
        _stats["empty_content"] += 1


def record_degrade(scope: str):
    """记录一次业务级静默降级（如意图快判超时、改写跑偏回退、改写异常回退）。

    这些不是 provider 失败（不触发跨 provider 降级），而是调用方主动放弃 LLM 结果、
    回退规则/原 query 的「静默降级」。此前无埋点，运维无法发现「意图分类/改写
    持续失败」这类系统性症状，故单独计数并纳入 degradation_rate()。
    """
    with _lock:
        _stats["degrades"][scope] = _stats["degrades"].get(scope, 0) + 1
    logger.debug(f"[LLM审计] 业务降级 scope={scope}")


def get_stats() -> dict:
    """返回聚合统计快照（供 /api/health 扩展或诊断）"""
    with _lock:
        return {
            "calls": dict(_stats["calls"]),
            "failures": dict(_stats["failures"]),
            "retries": _stats["retries"],
            "empty_content": _stats["empty_content"],
            "degrades": dict(_stats["degrades"]),
            "total_tokens_in": _stats["total_tokens_in"],
            "total_tokens_out": _stats["total_tokens_out"],
            "total_ms": round(_stats["total_ms"], 1),
        }


def degradation_rate() -> dict:
    """各 provider 的降级率（失败/总调用）+ 业务级降级计数，用于健康度体温计"""
    with _lock:
        rates = {}
        for p, c in _stats["calls"].items():
            f = _stats["failures"].get(p, 0)
            rates[p] = round(f / (c + f), 4) if (c + f) else 0.0
        # 业务级降级单独暴露为原值（不是比率），供 health 端点观察到「非 LLM 故障的静默降级」
        rates["_business_degrades"] = dict(_stats["degrades"])
        return rates
