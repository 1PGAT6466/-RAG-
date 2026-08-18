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
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TIMEOUT,
)

logger = logging.getLogger("rag.chat")

SYSTEM_PROMPT = """你是一个工业知识库助手。根据提供的文档内容回答用户问题。

规则：
1. 只基于下方「参考资料」中的内容回答，不要编造信息
2. 每个关键结论后，用 [编号] 标注其依据的参考资料编号（如 [1]、[2][3]）
3. 编号必须是参考资料里明确给出的序号，不得杜撰不存在的编号
4. 如果参考资料中没有相关信息，明确告知用户，不要强行引用
5. 回答简洁、准确、结构化，引用标注紧跟在对应句末"""


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
        if not content or content in seen:
            continue
        seen.add(content)
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


async def generate(query: str, context: list[dict]) -> tuple[str, list[dict]]:
    """
    组装 prompt → 调用 LLM → 返回 (answer, refs)

    refs：引用标注映射（编号 → chunk 精确位置），供前端渲染脚注。
    向后兼容：调用方若只取 answer（如旧代码 `answer = await generate(...)`），
    会拿到 tuple，需同步更新调用方（api.py 已更新）。
    """
    # 组装带编号上下文 + 引用映射
    context_text, refs = _build_reference_context(context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"参考资料：\n\n{context_text}\n\n问题：{query}"}
    ]

    # 优先 MiMo，失败切 DeepSeek；两者均失败时抛统一错误（交给全局异常处理器）
    try:
        answer = await _call_mimo(messages)
    except Exception as e:
        logger.warning(f"MiMo 调用失败，降级 DeepSeek: {e}")
        try:
            answer = await _call_deepseek(messages)
        except Exception as e2:
            logger.error(f"DeepSeek 也失败: {e2}")
            raise RuntimeError("LLM 调用失败（MiMo + DeepSeek 均不可用）") from e2

    if not answer or not answer.strip():
        # 推理型模型可能返回空 content（reasoning 抢占 max_tokens），给个可读的降级提示
        logger.warning("LLM 返回空 content，使用降级提示")
        answer = "抱歉，模型暂未返回有效回答，请稍后重试。"

    return answer, refs


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


async def _call_mimo(messages: list[dict]) -> str:
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
                "max_tokens": 2048,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _call_deepseek(messages: list[dict]) -> str:
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
                "max_tokens": 2048,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
