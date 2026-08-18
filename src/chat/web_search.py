"""
联网搜索 — Tavily 搜索（第二阶段：联网模式）

设计原则：
  - 简洁：只做「搜索 → 返回结构化结果」，供 LLM 综合
  - 降级：搜索失败/超时 → 返回空列表，由调用方降级为闲聊并提示
  - 结构化：保留 title / url / content 三要素，便于 LLM 引用来源链接
"""
import logging
import httpx
from config import TAVILY_API_KEY

logger = logging.getLogger("rag.websearch")

TAVILY_URL = "https://api.tavily.com/search"


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Tavily 搜索，返回 [{title, url, content}, ...]。
    失败返回空列表（不抛异常，由调用方降级处理）。
    """
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY 未配置，无法联网搜索")
        return []

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",  # basic 更快；advanced 更准但慢
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": (r.get("content", "") or "")[:500],
            })
        logger.info(f"联网搜索 '{query[:30]}' -> {len(results)} 条结果")
        return results
    except Exception as e:
        logger.warning(f"联网搜索失败: {e}")
        return []


def build_web_context(results: list[dict]) -> str:
    """把搜索结果组装成给 LLM 的带编号上下文"""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['title']}\n{r['content']}\n来源: {r['url']}")
    return "\n\n".join(parts)
