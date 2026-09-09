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

# CORS：逗号分隔的允许来源；默认本地开发地址（不允许 allow_origins=* + allow_credentials=True 的危险组合）
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]

# 路径
DATA_DIR = BASE_DIR / _env("DATA_DIR", "data")
UPLOAD_DIR = BASE_DIR / _env("UPLOAD_DIR", "data/uploads")
IMAGES_DIR = BASE_DIR / _env("IMAGES_DIR", "data/images")
DB_PATH = str(BASE_DIR / _env("DB_PATH", "data/rag.db"))

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

# 检索
SEARCH_TOP_K = int(_env("SEARCH_TOP_K", "10"))
BM25_WEIGHT = float(_env("BM25_WEIGHT", "0.5"))
VECTOR_WEIGHT = float(_env("VECTOR_WEIGHT", "0.5"))

# 阶段 1 替换开关：ChromaDB 向量存储 / jieba 分词（默认开启，置 0 回退旧实现）
RAG_CHROMA = _env("RAG_CHROMA", "1")
RAG_JIEBA = _env("RAG_JIEBA", "1")

# 检索增强开关
RAG_DYNAMIC_RANKING = _env("RAG_DYNAMIC_RANKING", "1")
RAG_RERANK = _env("RAG_RERANK", "1")

# 图谱检索召回（实体导航，第三个召回源；默认开启，置 0 关闭）
RAG_GRAPH_RECALL = _env("RAG_GRAPH_RECALL", "1")

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

# 语言归一化：高置信度繁转简 + 非中文（日/韩/乱码）过滤
RAG_LANG_FILTER = _env("RAG_LANG_FILTER", "1")

# 流式入库（大 PDF 边解析边入库）
RAG_STREAM_INGEST = _env("RAG_STREAM_INGEST", "1")
RAG_STREAM_MIN_PAGES = _env("RAG_STREAM_MIN_PAGES", "20")
RAG_STREAM_FLUSH_PAGES = _env("RAG_STREAM_FLUSH_PAGES", "20")
RAG_IMAGE_EXTRACT = _env("RAG_IMAGE_EXTRACT", "1")  # 图片提取（可读模式 ![[图]] 显示）
