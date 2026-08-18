"""
意图路由 — 对话前判断问题类型（knowledge / chat / web）

设计原则：
  - 三层降级：规则关键词（零成本快判）→ LLM 快判（可选，用非推理模型）→ 保守默认 knowledge
  - 保守默认：判不准时走 knowledge（RAG），宁可查库也不乱联网/乱聊
  - web 意图在第一阶段仅识别不执行（联网模式需 Tavily key，第二批接入）；
    识别为 web 但未配置搜索时，降级为 chat（自由对话并告知无法联网）

Feature Flag: RAG_INTENT_LLM（默认 1，用 deepseek-v4-flash 快判；置 0 纯规则）
"""
import re
import logging

logger = logging.getLogger("rag.intent")

# ============ 规则关键词 ============
# 闲聊触发词（寒暄/通用对话）
_CHAT_PATTERNS = [
    r'^(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|谢谢|多谢|再见|拜拜|早上好|晚上好|下午好)',
    r'(你是谁|你叫什么|介绍一下你自己|你能做什么|你叫什么名字)',
    r'(讲个笑话|聊聊天|陪我聊|随便聊聊)',
]

# 联网触发词（实时/外部信息需求）。识别后第一阶段若无搜索 key 则降级 chat。
_WEB_PATTERNS = [
    r'(今天|现在|最近|目前|最新).{0,6}(天气|气温|新闻|股价|汇率|金价|油价|热点)',
    r'(天气|气温|温度)',
    r'(最新|今日|实时).{0,4}(新闻|资讯|行情|动态)',
    r'(谁.{0,6}(是|当)|什么.{0,6}(人|公司|组织)).{0,10}',
    r'(百度|谷歌|搜索|查一下|帮我查).{0,10}',
]

_CAT_RE = [re.compile(p) for p in _CHAT_PATTERNS]
_WEB_RE = [re.compile(p) for p in _WEB_PATTERNS]


def _rule_classify(query: str) -> str:
    """规则快判：chat > web 优先级（chat 更明确），否则 knowledge"""
    q = query.strip()
    for pat in _CAT_RE:
        if pat.search(q):
            return "chat"
    for pat in _WEB_RE:
        if pat.search(q):
            return "web"
    return "knowledge"


def classify_intent(query: str) -> str:
    """
    意图分类，返回 'knowledge' | 'chat' | 'web'。

    第一阶段实现：纯规则分类（零成本、无外部依赖、可离线）。
    LLM 快判留待后续（用 deepseek-v4-flash，减少误判），当前规则已覆盖常见场景。
    """
    intent = _rule_classify(query)
    logger.info(f"意图分类: '{query[:30]}' -> {intent}")
    return intent
