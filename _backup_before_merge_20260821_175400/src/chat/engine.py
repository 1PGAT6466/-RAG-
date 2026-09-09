"""
对话引擎 — 上下文拼接 + LLM 生成（带知识点引用标注）

设计原则（对齐"伏羲"统一/有序/稳定）：
  - 统一   ：generate 返回 (answer, refs)，refs 是编号→chunk 的精确映射，
             与 api_chat 的 sources 结构对齐，前端可渲染脚注 [1][2] 并跳转
  - 有序   ：检索结果先编号、再进 prompt，LLM 强制用 [编号] 标注引用
  - 稳定   ：MiMo 优先，失败降级 DeepSeek；引用编号由代码生成（非 LLM 杜撰），
             杜绝"编造不存在的引用"
"""
import logging
import httpx
from config import (
    MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_FLASH_MODEL, DEEPSEEK_TIMEOUT,
)

logger = logging.getLogger("rag.chat")

SYSTEM_PROMPT = """你是一个工业知识库助手。根据提供的文档内容回答用户问题。

规则：
1. 只基于下方「参考资料」中的内容回答，不要编造信息
2. 每个关键结论后，用 [编号] 标注其依据的参考资料编号（如 [1]、[2][3]）
3. 编号必须是参考资料里明确给出的序号，不得杜撰不存在的编号
4. 如果参考资料中没有相关信息，明确告知用户，不要强行引用
5. 回答简洁、准确、结构化，引用标注紧跟在对应句末"""

# 闲聊模式用的系统提示（自由对话，无强制引用约束）
SYSTEM_PROMPT_CHAT = """你是一个友好、专业的工业知识库助手「伏羲」。

现在处于自由对话模式，你可以进行日常聊天、答疑、头脑风暴，不强制引用文档。
要求：
1. 回答友好、自然、有温度
2. 涉及工业/技术问题时给出专业、准确的解答
3. 不编造事实；若不确定，坦诚说明
4. 简洁有条理"""


def _build_reference_context(chunks: list[dict]) -> tuple[str, list[dict]]:
    """把检索结果组装成「带编号」的参考资料（供 LLM 引用标注）

    返回 (context_text, refs)，refs 为编号→chunk 的映射，含精确位置锚点。
    """
    parts = []
    seen = set()
    refs = []
    idx = 0
    for c in chunks:
        content = c.get("content", "")
        dedup_key = (c.get("id"), content)
        if not content or dedup_key in seen:
            continue
        seen.add(dedup_key)
        idx += 1
        fname = c.get("file_name", "未知文档")
        # 位置锚点：chunk_index（文档内第几段），有则标注
        loc = ""
        if c.get("chunk_index") is not None:
            loc = f"（第 {c['chunk_index']} 段）"
        parts.append(f"[{idx}]{loc}【{fname}】\n{content}")
        refs.append({
            "ref": idx,
            "file_id": c.get("file_id"),
            "file_name": fname,
            "chunk_id": c.get("id"),
            "chunk_index": c.get("chunk_index"),
            "content": content,
        })
    return "\n\n---\n\n".join(parts), refs


async def _call_llm_with_fallback(messages: list[dict], max_tokens: int = 1024) -> str:
    """统一 LLM 降级调用：DeepSeek Flash → DeepSeek Pro → MiMo。

    所有对话/生成路径共用此函数，避免三处重复降级逻辑。
    返回 answer 字符串；全部失败抛 RuntimeError，空 content 返回降级提示。
    """
    if not MIMO_API_KEY and not DEEPSEEK_API_KEY:
        logger.error("LLM 未配置：MIMO_API_KEY 与 DEEPSEEK_API_KEY 均为空")
        raise RuntimeError("LLM 服务未配置（缺失 API Key），请联系管理员在 .env 中配置")

    # DeepSeek flash（非推理、快、稳）→ DeepSeek pro → MiMo（推理型，最后兜底）
    try:
        answer = await _call_flash(messages, max_tokens=max_tokens)
    except Exception as e:
        logger.warning(f"DeepSeek flash 调用失败，降级 pro: {e}")
        try:
            answer = await _call_deepseek(messages, max_tokens=max_tokens)
        except Exception as e2:
            logger.warning(f"DeepSeek pro 也失败，降级 MiMo: {e2}")
            if not MIMO_API_KEY:
                raise RuntimeError("LLM 调用失败（DeepSeek 不可用且未配置 MiMo 作为备用）") from e2
            try:
                answer = await _call_mimo(messages, max_tokens=max_tokens)
            except Exception as e3:
                logger.error(f"MiMo 也失败: {e3}")
                raise RuntimeError("LLM 调用失败（flash + pro + MiMo 均不可用）") from e3

    if not answer or not answer.strip():
        logger.warning("LLM 返回空 content，使用降级提示")
        return "抱歉，模型暂未返回有效回答，请稍后重试。"
    return answer


async def generate(query: str, context: list[dict]) -> tuple[str, list[dict]]:
    """
    组装 prompt → 调用 LLM → 返回 (answer, refs)

    refs：引用标注映射（编号 → chunk 精确位置），供前端渲染脚注。
    """
    context_text, refs = _build_reference_context(context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"参考资料：\n\n{context_text}\n\n问题：{query}"}
    ]

    answer = await _call_llm_with_fallback(messages, max_tokens=1024)
    return answer, refs


async def generate_chat(query: str, history: list[dict] = None) -> str:
    """
    自由对话（闲聊模式）：无检索上下文，直接调 LLM。
    返回纯 answer 字符串（无引用）。
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT_CHAT}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": query})
    return await _call_llm_with_fallback(messages, max_tokens=1024)


# 联网模式系统提示：要求基于搜索结果回答并标注来源
SYSTEM_PROMPT_WEB = """你是一个工业知识库助手「伏羲」，现处于联网搜索模式。
根据下方「搜索结果」回答用户问题。
规则：
1. 综合多个搜索结果，给出准确、全面的回答
2. 关键结论后用 [编号] 标注对应结果（如 [1]、[2]）
3. 若搜索结果不足以回答，诚实说明并给出建议
4. 回答简洁、结构化"""


async def generate_web(query: str, search_results: list[dict]) -> tuple[str, list[dict]]:
    """
    联网模式：搜索 + LLM 综合，返回 (answer, sources)。
    sources 是 [{ref, title, url, content}]，供前端展示来源链接。
    """
    from .web_search import build_web_context
    context_text = build_web_context(search_results)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_WEB},
        {"role": "user", "content": f"搜索结果：\n\n{context_text}\n\n问题：{query}"}
    ]

    answer = await _call_llm_with_fallback(messages, max_tokens=1024)

    sources = [
        {
            "ref": i + 1,
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:200],
        }
        for i, r in enumerate(search_results)
    ]
    return answer, sources


def build_citation_sources(refs: list[dict], answer: str) -> list[dict]:
    """从 refs + answer 提取实际被引用的来源（供 api 返回精确锚点）

    解析 answer 中被引用的 [编号]，只返回真正被引用的来源。
    若 answer 无任何引用标注，返回全部 refs（降级，保证有事可看）。
    """
    import re
    cited = set(int(n) for n in re.findall(r'\[(\d+)\]', answer or ""))
    if not cited:
        # 无引用标注：返回全部 refs 作为兜底来源
        return refs
    mapping = {r["ref"]: r for r in refs}
    return [mapping[n] for n in sorted(cited) if n in mapping]


async def _call_flash(messages: list[dict], max_tokens: int = 1024) -> str:
    """DeepSeek flash：非推理、快、结构稳定，对话链路首选。"""
    async with httpx.AsyncClient(timeout=DEEPSEEK_TIMEOUT) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_FLASH_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _call_mimo(messages: list[dict], max_tokens: int = 2048) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{MIMO_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {MIMO_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MIMO_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _call_deepseek(messages: list[dict], max_tokens: int = 2048) -> str:
    async with httpx.AsyncClient(timeout=DEEPSEEK_TIMEOUT) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def generate_stream(query: str, context: list[dict]):
    """流式生成：yield 每个 token chunk，最后 yield __SOURCES__ + JSON"""
    import json as _json
    context_text, refs = _build_reference_context(context)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"参考资料：\n\n{context_text}\n\n问题：{query}"}
    ]
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("LLM 服务未配置")

    async with httpx.AsyncClient(timeout=DEEPSEEK_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_FLASH_MODEL, "messages": messages,
                  "max_tokens": 1024, "temperature": 0.3, "stream": True},
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
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
                except (KeyError, _json.JSONDecodeError):
                    continue
    yield "\n__SOURCES__" + _json.dumps(refs, ensure_ascii=False)
