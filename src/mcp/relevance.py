"""
mcp_relevance.py — MCP server 对「RAG 知识库文档管理系统」的相关性评分

用途：MCP 市场列表按「对 RAG 知识库最有提升效果」从大到小排序。

评分逻辑：把 RAG 系统的刚需能力拆成若干维度，每个维度配一组关键词
（精准短语，避免 search/data/query 这类泛词导致的误抬分），对 server 的
qualifiedName + displayName + description 做匹配，加权累加得到相关性分。

返回的 score 越高 = 对该 RAG 知识库越有用，列表按 score 降序（同分按 useCount 降序兜底）。
"""

# 能力维度：(维度名, 权重, [关键词...])
# 权重越高 = 对 RAG 知识库越刚需。关键词用短语，降低"search/data"泛词误伤。
DIMENSIONS = [
    # 向量检索 / 语义搜索 / 重排 —— RAG 检索核心
    ("向量语义检索", 8.0, [
        "vector search", "vector database", "vector db", "embedding",
        "semantic search", "similarity search", "rerank", "reranker",
        "nearest neighbor", "hnsw",
    ]),
    # 文档 / 文件系统 / 解析 —— 知识库入库刚需
    ("文档文件处理", 8.0, [
        "filesystem", "file system", "read files", "read and write",
        "pdf", "docx", "document", "documents", "office",
        "markdown", "text extraction", "extract text", "ocr",
        "parse", "parser", "file operations",
    ]),
    # 检索增强 / 知识库 / RAG 本体
    ("检索增强问答", 9.0, [
        "rag", "retrieval-augmented", "retrieval augmented",
        "knowledge base", "knowledgebase", "knowledge graph",
        "question answering", "question-answering", "qa system",
        "answer questions about", "answer questions",
    ]),
    # 网页搜索 / 联网检索 —— 检索增强的重要来源
    ("网页搜索联网", 7.0, [
        "web search", "search the web", "search engine",
        "internet search", "web crawling", "crawler", "crawl the web",
        "scrape", "scraping", "web fetch", "browse the web",
        "search and extract", "search and download",
    ]),
    # 数据库 / 结构化数据 —— 知识库底层存储
    ("数据库存储", 6.5, [
        "database", "sql", "postgres", "postgresql", "mysql", "sqlite",
        "mongodb", "bigquery", "run sql", "sql queries", "query database",
        "data warehouse", "data source",
    ]),
    # 知识图谱 / 实体抽取
    ("知识图谱实体", 7.5, [
        "knowledge graph", "entity extraction", "entity relationship",
        "neo4j", "ontology", "graph database",
        "named entity", "relationship extraction",
    ]),
    # 学术 / 论文文献 —— 专业领域知识增强
    ("学术文献", 5.5, [
        "arxiv", "academic paper", "academic", "scholarly", "scholar",
        "pubmed", "semantic scholar", "research paper", "literature",
        "scientific",
    ]),
    # 代码仓库 / API 文档问答
    ("代码文档", 4.5, [
        "code search", "source code", "repository",
        "api documentation", "read code", "codebase", "github code",
    ]),
]

# 已知 RAG 明星 server 的显式加权（qualifiedName 精确/前缀匹配），
# 用于在关键词之外补充对「品牌级刚需工具」的认可（如 Brave/Exa 搜索、云盘）
FEATURED_BOOST = [
    ("brave", 8.0),
    ("exa", 8.0),
    ("googledrive", 6.0),
    ("googlesheets", 5.0),
    ("filesystem", 9.0),
    ("fetch", 7.0),
    ("github", 4.0),
    ("memory", 7.0),
    ("sequential", 5.0),
    ("context7", 6.0),
    ("firecrawl", 7.0),
    ("tavily", 8.0),
    ("perplexity", 8.0),
    ("serpapi", 7.0),
    ("duckduckgo", 6.0),
    ("ddg", 6.0),
    ("wolfram", 5.0),
    ("wikipedia", 5.0),
    ("notion", 5.0),
    ("confluence", 5.0),
    ("sharepoint", 5.0),
    ("airtable", 4.5),
    ("mongodb", 5.0),
    ("supabase", 5.0),
    ("pinecone", 8.0),
    ("weaviate", 8.0),
    ("qdrant", 8.0),
    ("chroma", 8.0),
    ("milvus", 8.0),
    ("elasticsearch", 6.0),
    ("neo4j", 7.0),
]


def score_server(qualified_name: str, display_name: str = "", description: str = "") -> tuple[float, list[str]]:
    """对单个 MCP server 打分，返回 (score, 命中维度列表)。"""
    text = f"{qualified_name} {display_name} {description}".lower()
    qname = (qualified_name or "").lower()

    total = 0.0
    hit_dims: list[str] = []

    for dim, weight, kws in DIMENSIONS:
        # 每个维度只计一次权重（命中任一关键词即计入）——避免同一描述里
        # 堆叠同义关键词（search/download/extract）导致虚高刷分
        if any(k in text for k in kws):
            total += weight
            hit_dims.append(dim)

    # 品牌级明星工具显式加权（关键词不够突出时兜底）
    for name, boost in FEATURED_BOOST:
        if name in qname:
            total += boost
            break  # 只取最高命中的一个

    return round(total, 2), hit_dims
