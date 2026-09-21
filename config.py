"""
配置中心 — 从 .env 读取
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# 关闭 chromadb 遥测（必须在首次 import chromadb 之前设置）
# 根因：chromadb 0.6.3 是按 posthog-python 3.x API 写的（capture(distinct_id, event, props)），
#   但本机装的是 posthog 7.38.0（capture(event, **kwargs)），多传参数会报
#   "capture() takes 1 positional argument but 3 were given"。此报错纯属噪音（遥测无用），
#   直接静默 chromadb 的 telemetry logger 最干净，无需降级依赖。
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
import logging as _logging
_logging.getLogger("chromadb.telemetry").setLevel(_logging.CRITICAL)
_logging.getLogger("chromadb.telemetry.product.posthog").setLevel(_logging.CRITICAL)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _resolve_jwt_secret() -> str:
    """JWT 密钥：优先用 .env 强密钥；缺失时生成 64 字节随机密钥并写回 .env，
    绝不在代码里写死可猜测的弱默认。"""
    secret = os.getenv("JWT_SECRET", "")
    if secret and len(secret) >= 32:
        return secret
    import secrets
    if not secret:
        secret = secrets.token_hex(32)  # 64 字符（32 字节）
    # 写回 .env，保证重启后 token 仍有效
    env_path = BASE_DIR / ".env"
    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("JWT_SECRET="):
            lines[i] = f"JWT_SECRET={secret}"
            found = True
            break
    if not found:
        lines.append(f"JWT_SECRET={secret}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["JWT_SECRET"] = secret
    return secret


# 服务
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8099"))
JWT_SECRET = _resolve_jwt_secret()
JWT_EXPIRY_HOURS = int(_env("JWT_EXPIRY_HOURS", "24"))

# CORS：逗号分隔的允许来源；默认仅 localhost（内网 IP 请在 .env 用 CORS_ORIGINS 配置）
_DEFAULT_CORS = "http://localhost:4999,http://127.0.0.1:4999,http://localhost:8099,http://127.0.0.1:8099"
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()]

# 路径
DATA_DIR = BASE_DIR / _env("DATA_DIR", "data")
UPLOAD_DIR = BASE_DIR / _env("UPLOAD_DIR", "data/uploads")
IMAGES_DIR = BASE_DIR / _env("IMAGES_DIR", "data/images")
DB_PATH = str(BASE_DIR / _env("DB_PATH", "data/rag.db"))
SQLITE_BUSY_TIMEOUT = int(_env("SQLITE_BUSY_TIMEOUT", "5000"))  # SQLite 锁等待超时(ms)，全库统一出口

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# LLM
MIMO_API_KEY = _env("MIMO_API_KEY")
MIMO_BASE_URL = _env("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
MIMO_MODEL = _env("MIMO_MODEL", "mimo-v2.5")
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_FLASH_MODEL = _env("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT = int(_env("DEEPSEEK_TIMEOUT", "60"))

# 联网搜索（Tavily，第二阶段接入）
TAVILY_API_KEY = _env("TAVILY_API_KEY", "")

# SeedDMS 文档源（受控入库源头，见 docs/SeedDMS接入方案.md）
SEEDDMS_URL = _env("SEEDDMS_URL", "http://localhost:8080")
SEEDDMS_USER = _env("SEEDDMS_USER", "admin")
SEEDDMS_PASS = _env("SEEDDMS_PASS", "admin")
# SeedDMS 数据库（SQLite）与内容目录（直接写库路线，浏览器上传直接进 DMS）
# 宿主机路径：数据库 content.db + 内容目录 1048576/（contentOffsetDir）
SEEDDMS_DB_PATH = _env("SEEDDMS_DB_PATH", r"E:\测试项目\SeedDMS\data\content.db")
SEEDDMS_CONTENT_DIR = _env("SEEDDMS_CONTENT_DIR", r"E:\测试项目\SeedDMS\data\1048576")
SEEDDMS_CONTENT_OFFSET = _env("SEEDDMS_CONTENT_OFFSET", "1048576")

# MCP 市场（Smithery）
SMITHERY_API_KEY = _env("SMITHERY_API_KEY", "")
SMITHERY_API_BASE = _env("SMITHERY_API_BASE", "https://api.smithery.ai")
# Embedding
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
EMBEDDING_DEVICE = _env("EMBEDDING_DEVICE", "cpu")
EMBED_REMOTE_THRESHOLD = int(_env("EMBED_REMOTE_THRESHOLD", "300"))
SILICONFLOW_API_KEY = _env("SILICONFLOW_API_KEY", "")
EMBEDDING_ONNX = _env("EMBEDDING_ONNX", "0")  # ONNX INT8 量化推理（2-4x CPU 加速）

# 语义缓存
SEMANTIC_CACHE = _env("SEMANTIC_CACHE", "1")  # 开关
SEMANTIC_CACHE_THRESHOLD = float(_env("SEMANTIC_CACHE_THRESHOLD", "0.92"))  # 相似度阈值
SEMANTIC_CACHE_MAX = int(_env("SEMANTIC_CACHE_MAX", "5000"))  # 最大缓存条目

# Adaptive-RAG 查询路由
RAG_ADAPTIVE = _env("RAG_ADAPTIVE", "1")  # 开关

# 上传
MAX_UPLOAD_SIZE_MB = int(_env("MAX_UPLOAD_SIZE_MB", "200"))
UPLOAD_TIMEOUT = int(_env("UPLOAD_TIMEOUT", "600"))
# #21（2026-09-21）：上传扩展名白名单（逗号分隔，小写含点）。空串表示不限制（回退旧行为）。
UPLOAD_ALLOWED_EXT = [
    e.strip().lower() for e in _env(
        "UPLOAD_ALLOWED_EXT",
        ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.md,.log,.json,.xml",
    ).split(",") if e.strip()
]

# 检索
SEARCH_TOP_K = int(_env("SEARCH_TOP_K", "10"))
BM25_WEIGHT = float(_env("BM25_WEIGHT", "0.5"))
VECTOR_WEIGHT = float(_env("VECTOR_WEIGHT", "0.5"))

# 检索运行参量（召回/重排可调优参数，统一出口；供在线配置面板与性能调优）
# 召回层
BM25_RECALL_LIMIT = int(_env("BM25_RECALL_LIMIT", "200"))      # BM25 召回候选池
BM25_PER_FILE = int(_env("BM25_PER_FILE", "8"))                 # BM25 每文件最多保留 chunk 数
VECTOR_RECALL_LIMIT = int(_env("VECTOR_RECALL_LIMIT", "50"))    # 向量召回候选数
GRAPH_RECALL_LIMIT = int(_env("GRAPH_RECALL_LIMIT", "50"))      # 图谱召回候选数
# 向量高置信门档（无词汇命中时保留高置信向量结果的阈值）
VECTOR_HIGH_CONF_THRESHOLD = float(_env("VECTOR_HIGH_CONF_THRESHOLD", "0.65"))
# 融合
CANDIDATE_K_MULTIPLIER = int(_env("CANDIDATE_K_MULTIPLIER", "3"))  # 中间候选池 top_k 倍数
RRF_K = int(_env("RRF_K", "60"))                                    # RRF 融合常数 k
# 重排
RERANK_TOP_K_MULTIPLIER = int(_env("RERANK_TOP_K_MULTIPLIER", "4"))  # rerank 候选 top_n 倍数

# 阶段 1 替换开关：ChromaDB 向量存储 / jieba 分词（默认开启，置 0 回退旧实现）
RAG_CHROMA = _env("RAG_CHROMA", "1")
RAG_JIEBA = _env("RAG_JIEBA", "1")

# 检索增强开关
RAG_DYNAMIC_RANKING = _env("RAG_DYNAMIC_RANKING", "1")
RAG_RERANK = _env("RAG_RERANK", "1")
# HyDE（Hypothetical Document Embedding）：语义模糊且 BM25/图谱双零召回时，用 LLM 生成
# 「假设答案」重新向量检索（兜底增强）。默认关闭（与 LLM 减负战略一致，仅在精准场景启用）。
RAG_HYDE = _env("RAG_HYDE", "0")
# Multi-query: LLM 改写为多个子查询分别检索后合并（默认关，评测通过再开）
RAG_MULTI_QUERY = _env("RAG_MULTI_QUERY", "0")

# 图谱检索召回（实体导航，第三个召回源；默认开启，置 0 关闭）
RAG_GRAPH_RECALL = _env("RAG_GRAPH_RECALL", "1")

# 反馈反哺检索（阶段二 B 方案：chunk 级 RRF 融合分惩罚 + 指数衰减 + 阈值）
#   默认关闭，灰度量后再开。详见 docs/audit/反馈反哺检索排序方案.md
RAG_FEEDBACK_ENABLE = _env("RAG_FEEDBACK_ENABLE", "0")
RAG_FEEDBACK_PENALTY_ALPHA = float(_env("RAG_FEEDBACK_PENALTY_ALPHA", "0.5"))  # 惩罚强度系数 α
RAG_FEEDBACK_HALF_LIFE_DAYS = float(_env("RAG_FEEDBACK_HALF_LIFE_DAYS", "7"))   # 半衰期（天）
RAG_FEEDBACK_THRESHOLD = float(_env("RAG_FEEDBACK_THRESHOLD", "2.0"))           # 生效阈值（有效点踩≥此值才惩罚）
RAG_FEEDBACK_HARD_DOWN = float(_env("RAG_FEEDBACK_HARD_DOWN", "3"))             # 硬阈值（≥此值穿透精确型号/authority 置顶）

# SiliconFlow（rerank + embedding）
SILICONFLOW_BASE_URL = _env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

# 实体抽取开关（规则抽取 / LLM 增强）
RAG_ENTITY_EXTRACT = _env("RAG_ENTITY_EXTRACT", "1")
RAG_ENTITY_LLM = _env("RAG_ENTITY_LLM", "0")  # 默认关，推理型慢且贵
RAG_ENTITY_LLM_WORKERS = _env("RAG_ENTITY_LLM_WORKERS", "2")  # LLM 抽取线程数
RAG_ENTITY_LLM_INTERVAL = _env("RAG_ENTITY_LLM_INTERVAL", "0.5")  # 请求间隔（秒）
RAG_ENTITY_LLM_MAX_CHUNKS = _env("RAG_ENTITY_LLM_MAX_CHUNKS", "50")  # 单文件最多抽取 chunk 数

# 入库后处理（引擎异步 Stage，默认开启）
RAG_AUTO_SUMMARY = _env("RAG_AUTO_SUMMARY", "1")
RAG_AUTO_TAG = _env("RAG_AUTO_TAG", "1")
RAG_AUTO_PREINDEX = _env("RAG_AUTO_PREINDEX", "1")
RAG_AUTO_SEMANTIC = _env("RAG_AUTO_SEMANTIC", "1")  # 语义边（compatible_process/标准字段/uses_standard）
RAG_AUTO_DOC_SIM = _env("RAG_AUTO_DOC_SIM", "1")  # 文档相似度边（similar）
RAG_CONTEXT_PREFIX = _env("RAG_CONTEXT_PREFIX", "0")  # Contextual Retrieval：为每个 chunk 添加上下文前缀（Anthropic 方案）

# PDF OCR（乱码/扫描件自动转 OCR）
RAG_PDF_OCR = _env("RAG_PDF_OCR", "auto")  # auto | force
RAG_OCR_DML = _env("RAG_OCR_DML", "1")  # DirectML GPU 加速开关

# 深度 PDF 解析（可选增强）：用 MinerU 对复杂 PDF（多栏/表格/公式）做结构化抽取。
# 默认 "0"（关闭）。MinerU 是重型依赖（需 magic_pdf 包 + 模型权重），未安装时自动
# 降级回 fitz+OCR 链路。启用后仅对「复杂 PDF」走 MinerU，简单 PDF 仍走轻量 fitz。
RAG_PDF_DEEP_PARSE = _env("RAG_PDF_DEEP_PARSE", "0")  # 0 | 1

# docling PDF 版面分析（可选增强）：用 docling 对 PDF 做结构化版面分析（表格/标题/图片识别）。
# 默认 "0"（关闭）。docling 是重型依赖（需 pip install docling，~500MB 含 PyTorch），未安装时自动
# 降级回 fitz+OCR 链路。启用后仅当 docling 产出 ≥50 字时采用，否则降级。
RAG_DOCLING_PDF = _env("RAG_DOCLING_PDF", "0")  # 0 | 1

# 语言归一化：高置信度繁转简 + 非中文（日/韩/乱码）过滤
RAG_LANG_FILTER = _env("RAG_LANG_FILTER", "1")

# 流式入库（大 PDF 边解析边入库）
RAG_STREAM_INGEST = _env("RAG_STREAM_INGEST", "1")
RAG_STREAM_MIN_PAGES = _env("RAG_STREAM_MIN_PAGES", "20")
RAG_STREAM_FLUSH_PAGES = _env("RAG_STREAM_FLUSH_PAGES", "20")
RAG_IMAGE_EXTRACT = _env("RAG_IMAGE_EXTRACT", "1")  # 图片提取（可读模式 ![[图]] 显示）

# 强制远程 embedding 的 PDF 页数阈值：流式入库时，总页数 ≥ 此值的 PDF 逐批 embed
# 也直接走 SiliconFlow 远程（而非本地 CPU）。根因：流式逐批（20 页/批）embed 时，
# 每批 chunk 数远小于 EMBED_REMOTE_THRESHOLD(300)，触发不了「超阈值走远程」，
# 导致 1423 页大 PDF 全程本地 CPU embedding（实测 68 分钟 + 3.3GB 内存，把服务拖垮）。
RAG_EMBED_FORCE_REMOTE_PAGES = int(_env("RAG_EMBED_FORCE_REMOTE_PAGES", "100"))

# 入库引擎并发上限：同时执行的重入库/解析任务数。
# 历史坑：enqueue 每任务开一个 daemon 线程且无上限，批量上传 N 个大文件会同时启动
# N 个线程，每个持有整本文本 + embedding + OCR，内存/CPU 爆炸导致 OOM/卡死。
# 默认 3 个并发，超限任务线程阻塞等待（仍保持 enqueue 立即返回 task_id 的契约）。
RAG_INGEST_MAX_CONCURRENT = int(_env("RAG_INGEST_MAX_CONCURRENT", "3"))
