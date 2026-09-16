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
from src.llm_client import call_llm, call_llm_stream

logger = logging.getLogger("rag.chat")

SYSTEM_PROMPT = """你是「伏羲」，一位熟悉资料的工业工程师。请认真阅读参考资料，全面、准确地回答用户问题。

规则：
1. 认真阅读全部参考资料，综合多条信息给出完整回答，不要只用一条就下结论。
2. 直接给出结论，不要用「根据参考资料」「可参考…章节」这类套话开头。
3. 用自然段落行文，回答要充实有料，不要敷衍了事。如果资料丰富，应该给出详尽的分析。
4. 引用标注用 [编号]，自然跟在对应句末（如「…推荐采用全屏蔽[1][3]」），编号必须是参考资料里已有的序号，不得杜撰。
5. 如果参考资料中没有相关信息，坦诚告知，不要强行引用或自圆其说。
6. 如果用户问的是关于知识库本身的问题（有多少文件、包含哪些内容），如实回答。
7. 简洁但不潦草——该详细的地方要详细，该简略的地方简略。"""

# 闲聊模式用的系统提示（自由对话，无强制引用约束）
SYSTEM_PROMPT_CHAT = """你是「伏羲」，一个温暖、有个性的工业知识库助手。

现在处于自由对话模式，不强制引用文档。你可以像一个真实的朋友一样聊天。

性格特点：
- 说话有温度、有情感共鸣，不是冷冰冰的机器
- 会用自然的口语化表达，偶尔带点幽默
- 对用户的情绪有感知，开心时一起开心，难过时给予安慰
- 坦诚、不做作，不确定的事情会说「我不太确定」
- 回答简洁但不冷漠，像朋友聊天而非客服

规则：
- 涉及工业/技术问题时保持专业准确
- 不编造事实
- 不要总是反问用户，主动给有价值的内容"""

# 聊天模式的单会话历史截断条数（统一口径，供 generate_chat 与 API 层共用，
#   避免历史「6 条 vs 20 条」两套截止口径不一致导致的多轮连续性差异）。
CHAT_HISTORY_LIMIT = 20


def _build_reference_context(chunks: list[dict], max_chunk_chars: int = 800,
                             max_total_chars: int = 6000) -> tuple[str, list[dict]]:
    """把检索结果组装成「带编号」的参考资料（供 LLM 引用标注）

    返回 (context_text, refs)，refs 为编号→chunk 的映射，含精确位置锚点。

    P7 增强：如果 chunk 携带 section_text（父段上下文），优先用父段替代子块碎片。
    父段截断到 max_chunk_chars，子块内容保留在 refs 中供引用溯源。

    上下文长度控制：单个 chunk 截断到 max_chunk_chars，总长度不超过 max_total_chars，
    避免大文档超长 chunk 白耗 token，把预算留给真正相关的片段。
    """
    parts = []
    seen = set()
    refs = []
    idx = 0
    total = 0
    for c in chunks:
        if total >= max_total_chars:
            break
        content = c.get("content", "") or ""
        content = content.strip()
        dedup_key = (c.get("id"), content)
        if not content or dedup_key in seen:
            continue
        seen.add(dedup_key)

        # P7: 父段优先 — 如果有 section_text 且比子块长，用父段替代
        section_text = (c.get("section_text") or "").strip()
        display_content = content
        if section_text and len(section_text) > len(content) * 1.5:
            display_content = section_text

        # 单 chunk 截断
        if len(display_content) > max_chunk_chars:
            display_content = display_content[:max_chunk_chars] + "……"
        idx += 1
        fname = c.get("file_name", "未知文档")
        loc = ""
        if c.get("chunk_index") is not None:
            loc = f"（第 {c['chunk_index']} 段）"
        block = f"[{idx}]{loc}【{fname}】\n{display_content}"
        parts.append(block)
        total += len(block)
        refs.append({
            "ref": idx,
            "file_id": c.get("file_id"),
            "file_name": fname,
            "chunk_id": c.get("id"),
            "chunk_index": c.get("chunk_index"),
            "content": content,  # 原始子块内容（供引用溯源）
            "parent_used": display_content != content,  # P7: 标记是否用了父段
        })
    return "\n\n---\n\n".join(parts), refs


async def _call_llm_with_fallback(messages: list[dict], max_tokens: int = 1024) -> str:
    """统一 LLM 降级调用，委托 src.llm。"""
    try:
        return await call_llm(messages, max_tokens=max_tokens)
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"LLM 调用异常: {e}")
        raise RuntimeError("LLM 调用失败") from e


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
        messages.extend(history[-CHAT_HISTORY_LIMIT:])
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


async def generate_web(query: str, search_results: list[dict], refined_context: str = None) -> tuple[str, list[dict]]:
    """
    联网模式：搜索 + LLM 综合，返回 (answer, sources)。
    sources 是 [{ref, title, url, content}]，供前端展示来源链接。

    refined_context: 经过 LLM 提纯的精简上下文（优先使用），
    未提供时回退到原始 build_web_context。
    """
    if refined_context:
        context_text = refined_context
    else:
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

    # S16: refined_context 路径下，只保留 answer 中实际引用的 sources
    if refined_context:
        import re as _re
        cited = set(int(n) for n in _re.findall(r'\[(\d+)\]', answer or ""))
        if cited:
            sources = [s for s in sources if s["ref"] in cited]

    return answer, sources


def build_citation_sources(refs: list[dict], answer: str) -> list[dict]:
    """从 refs + answer 提取实际被引用的来源（供 api 返回精确锚点）

    解析 answer 中被引用的 [编号]，只返回真正被引用的来源。
    若 answer 无任何引用标注，返回空列表——不退回全部 refs。

    理由（对齐「引用准确性」目标）：无引用标注意味着 LLM 未断言与文档的对应关系，
    此时若返回全部 refs，会把「没被引用」的来源也塞给前端，制造「有据可查」的假象，
    误导用户以为每个 refs 都支撑了这个回答。宁可空、不可虚。
    """
    import re
    cited = set(int(n) for n in re.findall(r'\[(\d+)\]', answer or ""))
    if not cited:
        return []
    mapping = {r["ref"]: r for r in refs}
    return [mapping[n] for n in sorted(cited) if n in mapping]


def check_citation_fidelity(refs: list[dict], answer: str) -> dict:
    """引用忠实度校验（Citation Fidelity）—— RAG 可信度核心。

    W9: 统一使用 validate_grounding 做检测，消除逻辑重复。
    """
    import re
    from src.chat.grounding import validate_grounding
    sources = [{"ref": r["ref"]} for r in refs]
    result = validate_grounding(answer, sources)
    warnings = []
    fuzzy = re.findall(r'(?:依据|根据|参阅|参考|见)\s*(?:资料|文档|数据|上|编号)\s*[\d一二三四五]?', answer or "")
    if fuzzy:
        warnings.append(f"存在 {len(fuzzy)} 处未规范标注的模糊引用（疑似）")
    return {
        "healthy": not result["removed"],
        "phantoms": [int(n) for n in result["removed"]],
        "warnings": warnings,
        "grounding_score": result["score"],
    }


def clean_phantom_citations(refs: list[dict], answer: str) -> str:
    """清洗杜撰引用编号：把 answer 中超出 refs 范围（越界）的 [编号] 剔除，返回清洗后的 answer。

    背景：LLM 可能输出超出 refs 数量的编号（refs 只 5 条，LLM 写 [6]），
    前端点击该脚注会得到空/错来源，损害可信度。此处做词级处理：
      - 越界编号（n > max_ref）：删除该 [n]（连同可能紧邻的逗号/顿号/空格）
      - 合法编号保留不动（含 [n][m] 连续形态）
    零 LLM、纯正则、不阻断；仅在答案真的含越界编号时才改动。
    """
    from src.chat.grounding import validate_grounding
    sources = [{"ref": r["ref"]} for r in refs]
    result = validate_grounding(answer, sources)
    if result["removed"]:
        logger.info(f"Grounding: 移除杜撰引用 {result['removed']}, 分数={result['score']}")
    return result["answer"]





async def generate_stream(query: str, context: list[dict]):
    """流式生成：yield 每个 token chunk，最后 yield __SOURCES__ + JSON。

    __SOURCES__ 事件用单行紧凑 JSON（ensure_ascii=True + 无分隔空格 + 去真实换行），
    从根上避免 refs 里的 content/file_name 含换行或特殊串（__SOURCES__/[DONE]）破坏
    SSE 逐 token 的 `data: {token}` 单行帧（历史线上断行 bug 的根因）。
    """
    import json as _json
    context_text, refs = _build_reference_context(context)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"参考资料：\n\n{context_text}\n\n问题：{query}"}
    ]
    try:
        async for token in call_llm_stream(messages, max_tokens=1024):
            yield token
    except Exception as e:
        logger.error(f"流式 LLM 调用异常: {e}")
        yield "\n\n⚠️ 生成中断，请重试。"
    # 单行安全：ensure_ascii 把换行/特殊字符都转成 \n 转义，separators 去空白，保证无反斜杠换行
    yield "__SOURCES__" + _json.dumps(refs, ensure_ascii=True, separators=(",", ":"))
