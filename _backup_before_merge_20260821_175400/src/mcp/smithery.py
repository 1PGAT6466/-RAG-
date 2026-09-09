"""
Smithery MCP 市场 registry 对接
==============================

对接 Smithery 公开 registry API（https://registry.smithery.ai），
提供 MCP server 的列表 / 搜索（服务端搜索 + 分页） / 详情查询。

只做「只读浏览」接口，不依赖 Smithery 的安装代理（安装由本项目 MCP 客户端负责）。

分页参数（registry 真实支持）：
- q        ：服务端关键词搜索（名称/描述）
- page     ：页码（从 1 开始）
- pageSize ：每页条数
返回结构含 pagination.totalCount / totalPages。
"""
import logging
import httpx

logger = logging.getLogger("rag.mcp.smithery")

SMITHERY_REGISTRY = "https://registry.smithery.ai/servers"


def _fetch_servers(q: str = "", page: int = 1, page_size: int = 20) -> dict:
    """拉取一页，返回 {servers: [...], total_count, total_pages}。失败返回空结构。"""
    try:
        # 同步 httpx（在 async 端点里用 run_in_executor 调，见 list_servers）
        with httpx.Client(timeout=20) as client:
            params = {"page": max(1, page), "pageSize": max(1, page_size)}
            if q:
                params["q"] = q
            resp = client.get(SMITHERY_REGISTRY, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"Smithery registry 拉取失败: {e}")
        return {"servers": [], "total_count": 0, "total_pages": 0}

    servers = data.get("servers", [])
    pag = data.get("pagination", {})
    return {
        "servers": servers,
        "total_count": pag.get("totalCount", len(servers)),
        "total_pages": pag.get("totalPages", 0),
    }


def _normalize(servers: list[dict]) -> list[dict]:
    """把 registry 原始字段规整成前端需要的结构。"""
    result = []
    for s in servers:
        name = s.get("qualifiedName") or s.get("namespace") or ""
        display = s.get("displayName") or name
        result.append({
            "qualifiedName": name,
            "displayName": display,
            "description": s.get("description") or "",
            "iconUrl": s.get("iconUrl", ""),
            "verified": s.get("verified", False),
            "useCount": s.get("useCount", 0),
            "homepage": s.get("homepage", ""),
        })
    return result


async def list_servers(query: str = "", page: int = 1, page_size: int = 20) -> dict:
    """
    市场列表：优先走本地缓存（秒回），缓存未就绪则触发后台预取 + 本次降级远程拉取。
    返回 {servers, total, total_pages, page, page_size, pending_translate}
    """
    import asyncio
    from src.mcp import local_cache

    page = max(1, page)
    page_size = min(max(1, page_size), 50)

    # 1) 缓存未就绪 -> 后台触发全量预取（异步，不阻塞本次请求）
    if not local_cache.is_cache_ready():
        local_cache.refresh_cache(force=False)

    # 2) 优先本地查询（按对 RAG 知识库的提升度降序排序）
    if local_cache.is_cache_ready():
        data = local_cache.query_local(query, page, page_size, sort_by_relevance=True)
    else:
        # 3) 缓存还没有（首次）-> 本次降级远程拉取一页，先给用户返回
        raw = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_servers, query, page, page_size
        )
        data = {
            "servers": _normalize(raw["servers"]),
            "total": raw["total_count"],
            "total_pages": raw["total_pages"],
            "page": page,
            "page_size": page_size,
        }

    servers = data["servers"]

    # 中文化：词典/缓存命中立即返回，未命中丢后台补译（不阻塞）
    pending = False
    try:
        from src.mcp.translate import translate_servers
        servers, pending = translate_servers(servers)
    except Exception as e:
        logger.warning(f"MCP 中文化失败，降级英文原文: {e}")

    return {
        "servers": servers,
        "total": data["total"],
        "total_pages": data["total_pages"],
        "page": page,
        "page_size": page_size,
        "pending_translate": pending,
    }


async def get_server(qualified_name: str) -> dict | None:
    """查单个 MCP server 详情（走服务端搜索 q 精确命中）。"""
    # 用 q 搜索，qualifiedName 精确等于目标即为匹配（服务端搜索会返回相关结果）
    try:
        import asyncio
        raw = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_servers, qualified_name, 1, 20
        )
        for s in raw["servers"]:
            name = s.get("qualifiedName") or s.get("namespace") or ""
            if name == qualified_name:
                norm = _normalize([s])[0]
                return norm
    except Exception as e:
        logger.warning(f"get_server({qualified_name}) 失败: {e}")
    return None


def _fetch_server_full(qualified_name: str) -> dict | None:
    """用 SMITHERY_API_KEY 调 api.smithery.ai/servers/<name> 拿完整详情。

    返回含 remote / deploymentUrl / connections(含 configSchema) / tools 的结构。
    """
    from config import SMITHERY_API_KEY, SMITHERY_API_BASE
    if not SMITHERY_API_KEY:
        logger.warning("未配置 SMITHERY_API_KEY，无法拉取 server 完整详情")
        return None
    try:
        with httpx.Client(timeout=25) as client:
            resp = client.get(
                f"{SMITHERY_API_BASE}/servers/{qualified_name}",
                headers={"Authorization": f"Bearer {SMITHERY_API_KEY}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"get_server_full({qualified_name}) 失败: {e}")
        return None


async def get_server_full(qualified_name: str) -> dict | None:
    """异步拉 server 完整详情（含 remote/deploymentUrl/configSchema/tools）。"""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, _fetch_server_full, qualified_name
    )


def resolve_mcp_url(qualified_name: str, namespace: str = "") -> str:
    """推算 remote server 的公开直连 MCP URL。

    Smithery 部分 server（如 exa）提供 https://mcp.<namespace>.ai 公开端点；
    大多数 remote server 的 deploymentUrl（.run.tools）需 Smithery OAuth，不能直连。
    """
    ns = namespace or qualified_name.split("/")[0]
    return f"https://mcp.{ns}.ai"
