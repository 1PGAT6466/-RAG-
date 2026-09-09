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


async def rewrite_query(query: str) -> str:
    """Rewrite-Retrieve-Read: 用 LLM 将用户口语化查询改写为更适合检索的技术查询。

    例如：
      "怎么防锈" → "金属防锈工艺 防锈处理方法 表面处理标准"
      "那个连接器的参数" → "连接器型号规格参数 阻抗频率额定电压"

    延迟开销: ~0.1-0.3s (DeepSeek Flash)。
    失败时静默返回原查询（降级不阻断）。
    """
    try:
        from src.chat.engine import _call_llm_with_fallback
        prompt = (
            f"将以下用户问题改写为 2-3 个更适合技术文档检索的关键词短语，"
            f"用空格分隔，不要解释不要标点：\n\n{query}"
        )
        rewritten = await _call_llm_with_fallback([
            {"role": "system", "content": "你是检索查询优化器。只输出改写后的查询词，不要其他内容。"},
            {"role": "user", "content": prompt},
        ], max_tokens=100)
        if rewritten and len(rewritten.strip()) > 2:
            result = rewritten.strip().replace("\n", " ")
            logger.info(f"查询改写: '{query[:30]}' -> '{result[:50]}'")
            return result
    except Exception as e:
        logger.debug(f"查询改写失败（降级用原查询）: {e}")
    return query


# ============ Adaptive-RAG 查询复杂度路由 ============

# 简单查询模式（不需要检索，直接用 LLM 知识回答）
_SIMPLE_PATTERNS = [
    r'^(你好|您好|hi|hello|谢谢|再见)',
    r'(你是谁|你叫什么|你能做什么)',
    r'(什么是|解释一下|定义).{0,6}(的概念|的意思|的定义)?$',
    r'^(帮忙|帮我|请).{0,4}(翻译|计算|转换|换算)',
]

# 复杂查询模式（需要多步检索或多文档推理）
_COMPLEX_PATTERNS = [
    r'(对比|比较|区别|优缺点|哪个更好)',
    r'(为什么|原因|原理|机理)',
    r'(怎么做|如何实现|步骤|流程|方法).{0,10}(详细|具体|完整)',
    r'(分析|评估|评价|建议).{0,8}(方案|策略|选择)',
]

_SIMPLE_RE = [re.compile(p) for p in _SIMPLE_PATTERNS]
_COMPLEX_RE = [re.compile(p) for p in _COMPLEX_PATTERNS]


def classify_complexity(query: str) -> str:
    """Adaptive-RAG: 将查询分为 simple / medium / complex。

    - simple: 不需要检索，LLM 直接回答（节省检索+嵌入成本）
    - medium: 单步检索
    - complex: 多步检索或查询改写后检索

    Returns: 'simple' | 'medium' | 'complex'
    """
    from config import RAG_ADAPTIVE
    if RAG_ADAPTIVE != "1":
        return "medium"  # 关闭时默认中等（走正常检索）

    q = query.strip()

    # 短查询（< 5 字）直接走检索（可能是型号/关键词）
    if len(q) < 5:
        return "medium"

    for pat in _SIMPLE_RE:
        if pat.search(q):
            logger.info(f"Adaptive-RAG: simple (规则) query='{q[:30]}'")
            return "simple"

    for pat in _COMPLEX_RE:
        if pat.search(q):
            logger.info(f"Adaptive-RAG: complex (规则) query='{q[:30]}'")
            return "complex"

    return "medium"
