"""
联网搜索 — Tavily 搜索 + 相关性过滤 + LLM 摘要提纯

流程：
  1. Tavily 搜索（advanced + raw_content）
  2. 关键词相关性过滤（去噪）
  3. LLM 摘要提纯（每条结果提取关键句，综合成精简上下文）
  4. 喂给生成 LLM 综合回答

降级：任一步失败回退到上一步的结果。
"""
import logging
import re
import httpx
from config import TAVILY_API_KEY

logger = logging.getLogger("rag.websearch")

TAVILY_URL = "https://api.tavily.com/search"


async def web_search(query: str, max_results: int = 10) -> list[dict]:
    """
    Tavily 搜索，返回 [{title, url, content}, ...]。
    失败返回空列表（不抛异常，由调用方降级处理）。
    """
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY 未配置，无法联网搜索")
        return []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "advanced",
                    "include_raw_content": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for r in data.get("results", []):
            raw = (r.get("raw_content") or "").strip()
            content = raw if len(raw) > 200 else (r.get("content") or "")
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": content[:3000],
            })
        logger.info(f"联网搜索 '{query[:30]}' -> {len(results)} 条结果")
        return results
    except Exception as e:
        logger.warning(f"联网搜索失败: {e}")
        return []


def filter_by_relevance(query: str, results: list[dict], min_score: float = 0.15) -> list[dict]:
    """关键词相关性过滤（零 LLM，纯规则）

    用 query 分词后的关键词命中率打分，低于阈值的结果丢弃。
    保证至少保留 2 条结果（避免过度过滤）。
    """
    if not results:
        return []

    # 提取 query 关键词（中文按字/词切分 + 英文单词）
    keywords = _extract_keywords(query)
    if not keywords:
        return results

    scored = []
    for r in results:
        text = (r.get("title", "") + " " + r.get("content", "")).lower()
        hits = sum(1 for kw in keywords if kw in text)
        score = hits / len(keywords)
        scored.append((score, r))

    # 按相关性降序排列
    scored.sort(key=lambda x: x[0], reverse=True)

    # 过滤：得分 >= min_score，至少保留 2 条
    filtered = [r for score, r in scored if score >= min_score]
    if len(filtered) < 2:
        filtered = [r for _, r in scored[:2]]

    logger.info(f"相关性过滤: {len(results)} -> {len(filtered)} 条 (关键词: {keywords})")
    return filtered


def _extract_keywords(query: str) -> list[str]:
    """从查询中提取关键词（中英文混合）"""
    # 英文单词（3+ 字母）
    en_words = [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', query)]
    # 中文连续字符（2+ 字，作为短语匹配）
    zh_phrases = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    # 中文单字（补充，权重较低）
    zh_chars = re.findall(r'[\u4e00-\u9fff]', query)

    keywords = en_words + zh_phrases
    # 如果关键词太少，加入单字
    if len(keywords) < 3:
        keywords += zh_chars[:5]
    return keywords


async def refine_web_results(query: str, results: list[dict]) -> str:
    """LLM 摘要提纯：从搜索结果中提取与 query 相关的关键信息。

    返回精简的上下文文本（供生成 LLM 使用），失败返回原始 build_web_context。
    """
    if not results:
        return ""

    # 构造提纯 prompt
    raw_context = _build_raw_context(results)
    prompt = _build_refine_prompt(query, raw_context)

    try:
        from src.llm_client import call_llm
        refined = await call_llm(prompt, max_tokens=2000)
        if refined and len(refined.strip()) > 50:
            logger.info(f"搜索结果提纯完成: {len(raw_context)} -> {len(refined)} 字")
            return refined.strip()
    except Exception as e:
        logger.warning(f"LLM 提纯失败（降级用原文）: {e}")

    # 降级：用原始上下文
    return build_web_context(results)


def _build_raw_context(results: list[dict]) -> str:
    """把搜索结果拼成带编号的原始上下文"""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['title']}\n{r['content']}")
    return "\n\n".join(parts)


def _build_refine_prompt(query: str, raw_context: str) -> str:
    """构造提纯 prompt"""
    return f"""你是信息提纯助手。从以下搜索结果中，提取与问题直接相关的关键信息。

要求：
1. 只保留与问题相关的内容，丢弃无关部分
2. 保留关键数据、参数、事实（数字/型号/标准号必须原文保留）
3. 每条来源标注编号 [1][2]...
4. 综合成结构化的要点列表，不要写成文章
5. 如果某条结果完全无关，直接跳过不提

问题：{query}

搜索结果：
{raw_context[:12000]}

提取的关键信息（要点列表）："""


def build_web_context(results: list[dict]) -> str:
    """把搜索结果组装成给 LLM 的带编号上下文（原始版本）"""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['title']}\n{r['content']}\n来源: {r['url']}")
    return "\n\n".join(parts)
