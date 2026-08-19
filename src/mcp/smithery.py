"""
Smithery MCP 市场 registry 对接
==============================

对接 Smithery 公开 registry API（https://registry.smithery.ai），
提供 MCP server 的列表 / 搜索 / 详情查询。

只做「只读浏览」接口，不依赖 Smithery 的安装代理（安装由本项目 MCP 客户端负责）。
"""
import logging
import httpx

logger = logging.getLogger("rag.mcp.smithery")

SMITHERY_REGISTRY = "https://registry.smithery.ai/servers"


async def list_servers(query: str = "", limit: int = 50) -> list[dict]:
    """
    从 Smithery registry 拉取 MCP server 列表。
    返回 [{qualifiedName, displayName, description, iconUrl, verified, useCount, homepage}, ...]
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(SMITHERY_REGISTRY)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Smithery registry 拉取失败: {e}")
        return []

    servers = data.get("servers", [])
    result = []
    for s in servers:
        name = s.get("qualifiedName") or s.get("namespace") or ""
        display = s.get("displayName") or name
        desc = s.get("description") or ""
        # 关键词过滤（名称/描述）
        if query:
            q = query.lower()
            if q not in name.lower() and q not in display.lower() and q not in desc.lower():
                continue
        result.append({
            "qualifiedName": name,
            "displayName": display,
            "description": desc,
            "iconUrl": s.get("iconUrl", ""),
            "verified": s.get("verified", False),
            "useCount": s.get("useCount", 0),
            "homepage": s.get("homepage", ""),
        })
        if len(result) >= limit:
            break
    return result


async def get_server(qualified_name: str) -> dict | None:
    """查单个 MCP server 详情"""
    servers = await list_servers(query="", limit=2000)
    for s in servers:
        if s["qualifiedName"] == qualified_name:
            return s
    return None
