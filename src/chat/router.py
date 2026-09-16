"""
意图路由 — 对话前判断问题类型（knowledge / chat / web）

设计原则：
  - 三层降级：规则关键词（零成本快判）→ LLM 快判（可选，用非推理模型）→ 保守默认 knowledge
  - 保守默认：判不准时走 knowledge（RAG），宁可查库也不乱联网/乱聊
  - web 意图在第一阶段仅识别不执行（联网模式需 Tavily key，第二批接入）；
    识别为 web 但未配置搜索时，降级为 chat（自由对话并告知无法联网）

当前实现：纯规则分类（零成本、无外部依赖、可离线）。
意图分类的 LLM 快判为预留能力，尚未启用。
"""
import re
import logging

logger = logging.getLogger("rag.intent")

# ============ 规则关键词 ============
# 闲聊触发词（寒暄/通用对话）
_CHAT_PATTERNS = [
    r'^(你好|您好|你好呀|hi|hello|嗨|哈喽|在吗|在不在|谢谢|多谢|感恩|再见|拜拜|早安|晚安|早上好|晚上好|下午好|中午好|你好啊)$',
    r'^(你是谁|你叫什么|介绍一下你自己|你能做什么|你叫什么名字|你叫什么|你是啥|你是谁呀|你的名字)',
    r'(讲个笑话|聊聊天|陪我聊|随便聊聊|说个笑话|讲个故事|夸夸我)',
    r'(我很|我有点|我最近)(开心|难过|无聊|累|烦|生气|焦虑|emo)',
]

# 元查询触发词（关于知识库本身的提问，无需检索，直接查数据库）
_META_PATTERNS = [
    r'(知识库|文档库|系统|库里).{0,10}(有|多少|总共|一共|几|多少个|有多少).{0,6}(文件|文档|资料|数据|条)',
    r'(有|多少|总共|一共|几).{0,6}(文件|文档|资料|条).{0,6}(在|上传|入库|知识库)',
    r'(知识库|文档库).{0,6}(包含|收录|存了|有)',
    r'(列出|展示|看看|显示).{0,6}(所有|全部|哪些).{0,4}(文件|文档)',
]

# 联网触发词（实时/外部信息需求）。识别后第一阶段若无搜索 key 则降级 chat。
_WEB_PATTERNS = [
    r'(今天|现在|最近|目前|最新|明天|未来几天).{0,8}(天气|气温|温度|下雨|下雪|降水|台风|晴|阴)',
    r'(天气|气温|温度|下雨|降雪|降水量)',
    r'(最新|今日|实时|当下).{0,4}(新闻|资讯|行情|动态|热点|时事)',
    r'(最近|今天|现在|有什么).{0,6}(新闻|热点|大事|疫情|政策)',
    r'(谁.{0,6}(是|当|当选)|什么.{0,6}(人|公司|组织|股票|基金)).{0,10}',
    r'(百度|谷歌|搜索|查一下|帮我查|上网查|搜一下).{0,10}',
    r'(股票|股价|大盘|涨停|跌停|汇率|金价|油价|比特币|基金净值|期货)',
    r'(世界杯|奥运会|比赛|赛果|比分|排名|体育赛事)',
]

_CAT_RE = [re.compile(p) for p in _CHAT_PATTERNS]
_WEB_RE = [re.compile(p) for p in _WEB_PATTERNS]
_META_RE = [re.compile(p) for p in _META_PATTERNS]


def _rule_classify(query: str) -> str:
    """规则快判：chat > meta > web 优先级（chat 更明确），否则 knowledge"""
    q = query.strip()
    for pat in _CAT_RE:
        if pat.search(q):
            return "chat"
    for pat in _META_RE:
        if pat.search(q):
            return "meta"
    for pat in _WEB_RE:
        if pat.search(q):
            return "web"
    return "knowledge"


def classify_intent(query: str) -> str:
    """
    意图分类，返回 'knowledge' | 'chat' | 'web'。

    纯规则分类（零成本、无外部依赖、可离线）。
    如需 LLM 快判纠偏（提高自然语言覆盖度），用 classify_intent_async。
    """
    intent = _rule_classify(query)
    logger.info(f"意图分类: '{query[:30]}' -> {intent}")
    return intent


# LLM 快判的 system prompt：严格三分类，只输出一个词。
_INTENT_SYSTEM = (
    "你是对话意图分类器。判断用户这句话属于哪一类，只输出一个词：\n"
    "- chat：闲聊/寒暄/情感表达/自我介绍/日常对话（不查资料、不查实时信息）\n"
    "- web：需要实时或外部信息（天气、新闻、股价、汇率、赛事、时事等联网才能拿到的内容）\n"
    "- knowledge：需要基于内部知识库/文档检索回答的专业问题\n"
    "只输出 chat、web 或 knowledge 这三个词之一，不要解释、不要标点。"
)


async def classify_intent_async(query: str) -> str:
    """意图分类（规则优先 + LLM 快判兜底）。

    规则命中 chat/web 直接返回（零 LLM 成本、低延迟）。
    规则未命中（默认 knowledge）时，用 Flash 模型快判一次二次确认——
    因为 knowledge 是保守默认，闲聊/联网被误判成 knowledge 是主要痛点，
    需要 LLM 兜底纠正。

    任何 LLM 异常/超时都静默降级回规则结果（knowledge），不阻断对话。
    """
    rule = _rule_classify(query)
    # 规则已明确命中 chat/web，直接返回（规则对这两类更可靠）
    if rule in ("chat", "web"):
        return rule
    # 规则默认 knowledge：用 LLM 快判二次确认，纠正常见的闲聊/联网误判
    try:
        from src.chat.engine import _call_llm_with_fallback
        import asyncio
        answer = await asyncio.wait_for(
            _call_llm_with_fallback([
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": query},
            ], max_tokens=256),  # 256：输出仅 1 个词，reasoning 放大后仍够用
            timeout=8.0,  # 快判独立短超时，避免拖慢对话
        )
        ans = (answer or "").strip().lower()
        for it in ("chat", "web", "knowledge"):
            if it in ans:
                logger.info(f"意图分类(LLM快判纠偏): '{query[:30]}' -> {it}")
                return it
    except Exception as e:
        logger.debug(f"意图分类 LLM 快判失败，回规则结果: {e}")
        try:
            from src import llm_audit
            llm_audit.record_degrade("intent_llm_fallback")
        except Exception:
            pass
    logger.info(f"意图分类: '{query[:30]}' -> {rule}")
    return rule


# 改写结果与原查询的分词重叠度阈值：低于此值视为改写跑偏，放弃改写用原查询。
_REWRITE_OVERLAP_THRESHOLD = 0.4

# rewrite_query 结果缓存：query 文本 → 改写结果（进程内 LRU，避免同一查询反复调 LLM）
import threading
from collections import OrderedDict
_rewrite_cache: OrderedDict = OrderedDict()
_rewrite_cache_lock = threading.Lock()
_REWRITE_CACHE_MAX = 256


# ============ 规则优先改写（LLM 减负：能规则命中就不调 LLM） ============
# 工业领域同义词/近义表述映射：把口语/俗名/简称扩展为标准技术检索词。
# 命中则直接规则改写，跳过 LLM。
# ⚠️ 只列「真同义」（如 防锈≈防腐蚀、镀金≈电镀）；
#   不列「泛化词」（选型/规格/参数/标准）——它们本身已是精确检索词，
#   扩张反而引入噪声（如「线性导轨选型」若把「选型」展开成「选型指南 型号规格 型号」，
#   会让召回偏到无关文档）。
_SYNONYM_EXPANSIONS = {
    "防锈": "防锈 防腐蚀 镀锌 表面处理",
    "防腐蚀": "防腐蚀 防锈 表面处理 镀层",
    "生锈": "生锈 腐蚀 氧化 防锈",
    "镀金": "镀金 电镀 金层 表面镀层",
    "镀层": "镀层 电镀 表面处理 镀金",
    "连接器": "连接器 接插件 端子 线束",
    "插头": "插头 连接器 接插件",
    "线束": "线束 线缆 连接器",
    "轴承": "轴承 滚动轴承 导轨",
    "导轨": "导轨 直线导轨 滑轨",
    "材料": "材料 材质 材料选型",
    "工艺": "工艺 加工 工艺流程",
    "强度": "强度 抗拉强度 屈服强度",
    "硬度": "硬度 硬度值 洛氏硬度 布氏硬度",
    "温度": "温度 工作温度 耐温",
    "电压": "电压 额定电压 耐压",
    "电流": "电流 额定电流 载流量",
    "阻抗": "阻抗 特性阻抗 电阻",
    "频率": "频率 工作频率 频段",
}

# 规则改写停用词（口语虚词，不参与关键词提取）
_REWRITE_STOPWORDS = {
    "怎么", "如何", "怎样", "什么", "哪里", "在哪", "为什么", "为何",
    "请问", "帮我", "麻烦", "一下", "一个", "这个", "那个", "哪些",
    "介绍", "说明", "解释", "讲讲", "都是", "多少", "有没有", "是否",
    "的", "了", "吗", "呢", "啊", "吧", "是", "在", "有", "和", "与",
}

_SC_REWRITE_STOP = re.compile("|".join(_REWRITE_STOPWORDS))


def _rule_rewrite(query: str):
    """纯规则改写：同义词扩展 + 去停用词提取关键词。

    返回规则改写结果字符串；若规则无法产出有效改进（无同义词命中、且原 query
    已是短/规范词），返回 None 表示「规则无能为力，交给 LLM」。
    """
    if not query or not query.strip():
        return None
    q = query.strip()

    # 1. 同义词命中：优先扩展
    hits = []
    for k, v in _SYNONYM_EXPANSIONS.items():
        if k in q:
            hits.append(v)
    if hits:
        # 同义词展开 + 保留原 query 实义词
        expanded = " ".join(hits)
        # 提取原 query 的实义关键词（分词后去停用词）
        try:
            import jieba
            jieba.setLogLevel(60)
            extra = [w for w in jieba.lcut(q) if len(w.strip()) >= 2 and w.strip() not in _REWRITE_STOPWORDS]
        except ImportError:
            extra = []
        result = " ".join(dict.fromkeys(expanded.split() + extra))
        return result

    # 2. 无同义词：检查 query 是否已是「规范技术词/精确实体」，是则直接返回原词（无需改写）
    #    判断标准：含型号/标准号/材料，或已是精炼关键词组合（短且实义）
    try:
        from src.retrieval.ranking import detect_exact_models
        if detect_exact_models(q):
            return q  # 已是精确实体，直接用原词
    except Exception:
        pass

    # 3. 去停用词后的关键词数量足够（≥2 个实义词），认为已是有效检索词，直接返回
    try:
        import jieba
        jieba.setLogLevel(60)
        terms = [w.strip() for w in jieba.lcut(q)
                 if len(w.strip()) >= 2 and w.strip() not in _REWRITE_STOPWORDS]
    except ImportError:
        terms = []
    if len(terms) >= 2:
        return " ".join(terms)

    # 4. 规则无能为力（纯口语/太短/含指代），交 LLM
    return None


def _cached_rewrite(query: str):
    """读 rewrite 缓存，命中返回改写串，未命中返回 None"""
    with _rewrite_cache_lock:
        if query in _rewrite_cache:
            val = _rewrite_cache.pop(query)
            _rewrite_cache[query] = val  # 移到末尾（LRU）
            return val
    return None


def _set_rewrite_cache(query: str, result: str):
    """写 rewrite 缓存，超限淘汰最旧条目"""
    with _rewrite_cache_lock:
        if query in _rewrite_cache:
            _rewrite_cache.pop(query)
        _rewrite_cache[query] = result
        while len(_rewrite_cache) > _REWRITE_CACHE_MAX:
            _rewrite_cache.popitem(last=False)


def _token_overlap(a: str, b: str) -> float:
    """计算两个字符串的分词重叠度（Jaccard over jieba tokens）。
    用于判断 LLM 改写是否仍然语义贴近原查询——改写跑偏会显著降低重叠度。
    """
    try:
        import jieba
        jieba.setLogLevel(60)
        toks_a = {t.strip().lower() for t in jieba.cut(a) if len(t.strip()) >= 1}
        toks_b = {t.strip().lower() for t in jieba.cut(b) if len(t.strip()) >= 1}
    except ImportError:
        toks_a = {t.lower() for t in a.split()}
        toks_b = {t.lower() for t in b.split()}
    if not toks_a or not toks_b:
        return 0.0
    inter = len(toks_a & toks_b)
    union = len(toks_a | toks_b)
    return inter / union if union else 0.0


async def rewrite_query(query: str) -> str:
    """Rewrite-Retrieve-Read: 用 LLM 将用户口语化查询改写为更适合检索的技术查询。

    例如：
      "怎么防锈" → "金属防锈工艺 防锈处理方法 表面处理标准"
      "那个连接器的参数" → "连接器型号规格参数 阻抗频率额定电压"

    安全网：改写结果与原查询分词重叠度 < _REWRITE_OVERLAP_THRESHOLD 时，
    判定改写跑偏（可能丢失原意），放弃改写、回退原查询，保证检索不因改写而劣化。

    延迟开销: ~0.1-0.3s (DeepSeek Flash)。
    失败时静默返回原查询（降级不阻断）。
    """
    # 缓存命中直接返回（同一 query 不重复调 LLM）
    cached = _cached_rewrite(query)
    if cached is not None:
        logger.debug(f"rewrite 缓存命中: '{query[:30]}'")
        return cached

    # 规则优先：能规则改写就不调 LLM（LLM 减负，降低延迟/成本，提升稳定性）
    rule_result = _rule_rewrite(query)
    if rule_result is not None and rule_result.strip():
        # 安全网：规则改写不能偏离原意（分词重叠度门槛），否则交 LLM 重新改写
        if rule_result.strip() == query.strip():
            _set_rewrite_cache(query, query)
            logger.info(f"规则改写（精确实体直用）: '{query[:30]}'")
            return query
        overlap = _token_overlap(query, rule_result)
        if overlap >= _REWRITE_OVERLAP_THRESHOLD:
            _set_rewrite_cache(query, rule_result)
            logger.info(f"规则改写: '{query[:30]}' -> '{rule_result[:50]}'（无 LLM）")
            return rule_result
        # 规则改写跑偏，回退到 LLM 改写
        logger.debug(f"规则改写重叠度 {overlap:.2f} 不足，交 LLM")

    try:
        from src.chat.engine import _call_llm_with_fallback
        prompt = (
            f"将以下用户问题改写为 2-3 个更适合技术文档检索的关键词短语，"
            f"用空格分隔，不要解释不要标点：\n\n{query}"
        )
        rewritten = await _call_llm_with_fallback([
            {"role": "system", "content": "你是检索查询优化器。只输出改写后的查询词，不要其他内容。"},
            {"role": "user", "content": prompt},
        ], max_tokens=512)  # 512：输出仅 20-50 字，reasoning 放大后仍够用
        if rewritten and len(rewritten.strip()) > 2:
            result = rewritten.strip().replace("\n", " ")
            # 安全网：改写跑偏检测
            overlap = _token_overlap(query, result)
            if overlap < _REWRITE_OVERLAP_THRESHOLD:
                logger.warning(
                    f"查询改写跑偏（重叠度 {overlap:.2f} < {_REWRITE_OVERLAP_THRESHOLD}），"
                    f"放弃改写: '{query[:30]}' -> '{result[:50]}'"
                )
                try:
                    from src import llm_audit
                    llm_audit.record_degrade("rewrite_drift")
                except Exception:
                    pass
                _set_rewrite_cache(query, query)
                return query
            _set_rewrite_cache(query, result)
            logger.info(f"查询改写: '{query[:30]}' -> '{result[:50]}' (重叠度 {overlap:.2f})")
            return result
    except Exception as e:
        logger.debug(f"查询改写失败（降级用原查询）: {e}")
        try:
            from src import llm_audit
            llm_audit.record_degrade("rewrite_error")
        except Exception:
            pass
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
