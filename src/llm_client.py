"""
llm.py — 统一 LLM 调用层
========================
所有模块共用此处调用 LLM，杜绝 5 处重复降级逻辑。

提供两个入口：
  - call_llm(messages, ...)        → async（对话/生成链路）
  - call_llm_sync(messages, ...)   → sync（后台线程/入库链路）

统一降级链：DeepSeek Flash → DeepSeek Pro → MiMo（可通过 prefer_mimo=True 反转）
"""
import logging
import httpx
import time
from config import (
    MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_FLASH_MODEL, DEEPSEEK_TIMEOUT,
)

logger = logging.getLogger("rag.llm")


def _build_provider_chain(prefer_mimo: bool = False) -> list[tuple[str, str, str, str, int]]:
    """返回 [(name, base_url, api_key, model, timeout), ...] 按优先级排列。

    默认：DeepSeek Flash → DeepSeek Pro → MiMo
    prefer_mimo=True：MiMo → DeepSeek Pro → DeepSeek Flash
    """
    # 快模型快速失败（20s），重型模型宽裕（40s），避免快模型等太久或慢模型被误杀
    _FLASH_TIMEOUT = min(DEEPSEEK_TIMEOUT, 20)
    _PRO_TIMEOUT = min(DEEPSEEK_TIMEOUT, 40)
    _MIMO_TIMEOUT = 60
    if prefer_mimo:
        return [
            ("mimo", MIMO_BASE_URL, MIMO_API_KEY, MIMO_MODEL, _MIMO_TIMEOUT),
            ("deepseek-pro", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, _PRO_TIMEOUT),
            ("deepseek-flash", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_FLASH_MODEL, _FLASH_TIMEOUT),
        ]
    return [
        ("deepseek-flash", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_FLASH_MODEL, _FLASH_TIMEOUT),
        ("deepseek-pro", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, _PRO_TIMEOUT),
        ("mimo", MIMO_BASE_URL, MIMO_API_KEY, MIMO_MODEL, _MIMO_TIMEOUT),
    ]


def _provider_headers(name: str, key: str) -> dict:
    """不同 provider 的认证 header 不同。MiMo 用 api-key，其他用 Bearer。"""
    if name == "mimo":
        return {"api-key": key, "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _reasoning_model_tokens(name: str, max_tokens: int) -> int:
    """推理型模型（含 reasoning_content 的 provider）需要更宽裕的 max_tokens 预算。

    MiMo 等 reasoning 模型的输出分 reasoning_content（思维链）+ content（正式回答）。
    若仍用非推理模型的 1024 预算，几乎必然被 reasoning 阶段耗尽，content 被截空，
    导致每次都要走一次「截空 → 大预算重试」的 double call（成本/延迟翻倍）。
    """
    if name == "mimo":
        return max(max_tokens, 4096)
    # DeepSeek v4 系列也是推理模型（有 reasoning_content），需要更宽裕的预算
    if "deepseek" in name:
        return max(max_tokens, 2048)
    return max_tokens


def _extract_content(choice: dict, name: str) -> tuple[str, str, bool]:
    """从 completion choice 提取 (content, reasoning_content, content_截空)。

    截空判定：content 为空但 reasoning_content 非空（推理完成、正式回答被 max_tokens 截断），
    或 finish_reason == 'length' 且 content 为空。
    """
    msg = choice.get("message", {}) or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    truncated = (not content.strip()) and (bool(reasoning.strip()) or choice.get("finish_reason") == "length")
    return content, reasoning, truncated


def _has_any_key() -> bool:
    return bool(MIMO_API_KEY or DEEPSEEK_API_KEY)


def _empty_answer_hint() -> str:
    return "抱歉，模型暂未返回有效回答，请稍后重试。"


# === Async 入口（对话/生成链路） ===

async def call_llm(
    messages: list[dict],
    max_tokens: int = 1024,
    temperature: float = 0.3,
    prefer_mimo: bool = False,
) -> str:
    """统一 async LLM 调用，自动降级。返回 answer 字符串；全部失败抛 RuntimeError。"""
    if not _has_any_key():
        raise RuntimeError("LLM 服务未配置（缺失 API Key），请联系管理员在 .env 中配置")

    chain = _build_provider_chain(prefer_mimo)
    last_err = None
    for name, base, key, model, timeout in chain:
        if not key:
            continue
        # P3: 熔断器检查
        from src.llm.circuit_breaker import get_breaker
        breaker = get_breaker(name)
        if breaker.is_open():
            logger.info(f"{name} 熔断中，跳过")
            continue
        if not breaker.acquire_token():
            logger.info(f"{name} 限流中，跳过")
            continue
        # 推理型模型需要更宽裕的 max_tokens 预算，避免稳态截空重试
        cur_max_tokens = _reasoning_model_tokens(name, max_tokens)
        try:
            _t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers=_provider_headers(name, key),
                    json={"model": model, "messages": messages, "max_tokens": cur_max_tokens, "temperature": temperature},
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                content, reasoning, truncated = _extract_content(choice, name)
                # 截空：reasoning 耗尽预算只剩思维链 / finish_reason=length 且 content 空。
                # 直接降级到下一个 provider，避免同一 provider 双倍调用成本。
                if not content.strip() and truncated:
                    logger.warning(f"{name} 因 max_tokens 截空 content（reasoning={len(reasoning)}字），降级下一个 provider")
                    from src import llm_audit
                    llm_audit.record_failure(name, "truncated_content")
                    breaker.record_failure()
                    continue
                if content.strip():
                    from src import llm_audit
                    llm_audit.record_call("call_llm", name, messages, content,
                                         (time.perf_counter() - _t0) * 1000)
                    breaker.record_success()
                    return content
                logger.warning(f"{name} 返回空 content")
                from src import llm_audit
                llm_audit.record_empty_content(name)
                breaker.record_failure()
        except Exception as e:
            logger.warning(f"{name} 调用失败: {e}")
            from src import llm_audit
            llm_audit.record_failure(name, str(e))
            breaker.record_failure()
            last_err = e

    raise RuntimeError(f"LLM 调用失败（所有 provider 均不可用）") from last_err


# === Async 流式入口 ===

async def call_llm_stream(
    messages: list[dict],
    max_tokens: int = 1024,
    temperature: float = 0.3,
    model: str = None,
    prefer_mimo: bool = False,
):
    """async 生成器，yield 每个 token。

    与非流式 call_llm 对齐：遍历 provider 降级链（Flash→Pro→MiMo），
    某个 provider 返回空/失败则降级下一个重新流式生成，从根上消除
    「流式主链路裸奔、无降级」的能力不对等。

    对推理型模型（reasoning_content 的 delta）跳过不 yield，只 yield 正式 content。
    """
    import json as _json
    if not _has_any_key():
        raise RuntimeError("LLM 服务未配置（缺失 API Key），请联系管理员在 .env 中配置")

    # 若显式指定 model，则只走该 model（保持向后兼容的覆盖语义）
    if model:
        chain = [("explicit", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, model, DEEPSEEK_TIMEOUT)]
    else:
        chain = _build_provider_chain(prefer_mimo)

    last_err = None
    for name, base, key, use_model, timeout in chain:
        if not key:
            continue
        # P3: 熔断器检查（与 call_llm 对齐）
        from src.llm.circuit_breaker import get_breaker
        breaker = get_breaker(name)
        if breaker.is_open():
            logger.info(f"{name} 熔断中，跳过")
            continue
        if not breaker.acquire_token():
            logger.info(f"{name} 限流中，跳过")
            continue
        cur_max_tokens = _reasoning_model_tokens(name, max_tokens)
        yielded_any = False
        try:
            _t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{base}/chat/completions",
                    headers=_provider_headers(name, key),
                    json={"model": use_model, "messages": messages, "max_tokens": cur_max_tokens,
                          "temperature": temperature, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = _json.loads(payload)
                            delta = chunk["choices"][0].get("delta", {}) or {}
                            token = delta.get("content", "")
                            if token:
                                yielded_any = True
                                yield token
                        except (KeyError, _json.JSONDecodeError):
                            continue
            if yielded_any:
                from src import llm_audit
                llm_audit.record_call("call_llm_stream", name, messages, "[stream]",
                                     (time.perf_counter() - _t0) * 1000)
                return
            # 整个流未产出任何 token（可能 reasoning 截空或空回复）→ 大预算重试一次再降级
            logger.warning(f"{name} 流式未产出任何 token，用大预算重试")
            from src import llm_audit
            llm_audit.record_empty_content(name)
            import asyncio
            await asyncio.sleep(0.1)
            retry_max = max(cur_max_tokens * 4, 8192)
            retry_yielded = False
            try:
                async with httpx.AsyncClient(timeout=timeout) as client2:
                    async with client2.stream(
                        "POST",
                        f"{base}/chat/completions",
                        headers=_provider_headers(name, key),
                        json={"model": use_model, "messages": messages, "max_tokens": retry_max,
                              "temperature": temperature, "stream": True},
                    ) as resp2:
                        resp2.raise_for_status()
                        async for line in resp2.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                break
                            try:
                                chunk = _json.loads(payload)
                                delta = chunk["choices"][0].get("delta", {}) or {}
                                token = delta.get("content", "")
                                if token:
                                    retry_yielded = True
                                    yield token
                            except (KeyError, _json.JSONDecodeError):
                                continue
            except Exception as re:
                logger.warning(f"{name} 大预算重试失败: {re}")
                breaker.record_failure()
            if retry_yielded:
                return
            logger.warning(f"{name} 大预算重试仍无 output，降级下一个 provider")
            breaker.record_failure()
        except Exception as e:
            logger.warning(f"{name} 流式调用失败: {e}")
            from src import llm_audit
            llm_audit.record_failure(name, str(e))
            breaker.record_failure()
            last_err = e

    raise RuntimeError("LLM 流式调用失败（所有 provider 均不可用）") from last_err


# === Sync 入口（后台线程/入库链路） ===

def call_llm_sync(
    messages: list[dict],
    max_tokens: int = 1024,
    temperature: float = 0.2,
    prefer_mimo: bool = False,
) -> str:
    """统一 sync LLM 调用，自动降级。返回 answer 字符串；全部失败返回空串。"""
    chain = _build_provider_chain(prefer_mimo)
    for name, base, key, model, timeout in chain:
        if not key:
            continue
        # P3: 熔断器检查
        from src.llm.circuit_breaker import get_breaker
        breaker = get_breaker(name)
        if breaker.is_open():
            continue
        if not breaker.acquire_token():
            continue
        cur_max_tokens = _reasoning_model_tokens(name, max_tokens)
        try:
            _t0 = time.perf_counter()
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{base}/chat/completions",
                    headers=_provider_headers(name, key),
                    json={"model": model, "messages": messages, "max_tokens": cur_max_tokens, "temperature": temperature},
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                content, reasoning, truncated = _extract_content(choice, name)
                # 截空：直接降级下一个 provider，不重试同一 provider
                if not content.strip() and truncated:
                    logger.warning(f"{name} content 被截空（reasoning={len(reasoning)}字），降级下一个 provider")
                    from src import llm_audit
                    llm_audit.record_failure(name, "truncated_content")
                    breaker.record_failure()
                    continue
                if content.strip():
                    from src import llm_audit
                    llm_audit.record_call("call_llm_sync", name, messages, content,
                                         (time.perf_counter() - _t0) * 1000)
                    breaker.record_success()
                    return content
                logger.warning(f"{name} 返回空 content")
                from src import llm_audit
                llm_audit.record_empty_content(name)
                breaker.record_failure()
        except Exception as e:
            logger.warning(f"{name} 调用失败: {e}")
            from src import llm_audit
            llm_audit.record_failure(name, str(e))
            breaker.record_failure()
            last_err = e
    # 全部 provider 耗尽仍空：记录明确的业务级降级告警（可观测），而非静默 return ""
    #   （调用方拿到空串会走各自的空值兜底，不会写脏数据；但运维侧需知道「LLM 全链空回复」发生）。
    from src import llm_audit
    llm_audit.record_degrade("llm_sync_all_empty")
    logger.warning("call_llm_sync: 所有 provider 均未返回有效内容，返回空串")
    return ""


# === JSON 提取工具 ===

def extract_json(text: str, expect: str = "auto"):
    """从 LLM 输出中提取 JSON 对象/数组，处理 markdown 代码块包裹。

    expect: "auto" | "object" | "array"
    返回解析后的 Python 对象，失败返回 None。
    """
    import json
    import re
    if not text:
        return None
    # 去掉 markdown 代码块包裹
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.replace("```", "").strip()
    # 尝试直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 按类型提取
    if expect == "array":
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    elif expect == "object":
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    else:
        # auto：先试 object 再试 array
        for l, r in [("{", "}"), ("[", "]")]:
            s, e = cleaned.find(l), cleaned.rfind(r)
            if s != -1 and e != -1:
                try:
                    return json.loads(cleaned[s:e + 1])
                except json.JSONDecodeError:
                    pass
    return None
