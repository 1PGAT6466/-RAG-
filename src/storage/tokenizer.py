"""
tokenizer.py — jieba 中文分词（替换手写 bigram）

用于 FTS5 全文检索的中文分词：
  - 入库时：content → jieba 词，用空格连接后写入 FTS（配合 unicode61 tokenizer）
  - 查询时：query → jieba 词，转成 FTS5 MATCH 短语查询

Feature Flag: RAG_JIEBA（默认 1，置 0 回退旧 bigram）
"""
import logging
import os

from config import RAG_JIEBA

logger = logging.getLogger("rag.tokenizer")

_jieba = None


def _load_jieba():
    global _jieba
    if _jieba is None:
        import jieba
        import jieba.analyse
        # 关闭日志噪音
        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    return _jieba


def _use_jieba() -> bool:
    return RAG_JIEBA == "1"


def segment(text: str) -> list[str]:
    """中文分词，返回词列表（去空、去纯标点/空白）"""
    if not text:
        return []
    jieba = _load_jieba()
    words = []
    for w in jieba.cut(text, cut_all=False):
        w = w.strip()
        if not w:
            continue
        # 过滤纯空白/标点
        if all(c in " \t\r\n，。、；：！？（）【】《》“”‘’—…·！？,.!?;:()[]{}<>\"'`~@#$%^&*+-=*/\\|" for c in w):
            continue
        words.append(w)
    return words


def segment_for_fts(text: str) -> str:
    """入库用：jieba 词用空格连接（供 unicode61 tokenizer 切分）"""
    if not _use_jieba():
        return text
    return " ".join(segment(text))


def _sanitize_fts_term(text: str) -> str:
    """转义 FTS5 短语内的特殊字符，防止注入或解析异常。

    FTS5 双引号短语内，双引号本身需转义为两个双引号（""）；
    同时剔除 FTS5 通配符 / 列过滤符等，避免非预期语法。
    """
    # 去掉 FTS5 语法字符（* ^ : { } 等在短语内仍有特殊含义）
    for ch in '*^:{}':
        text = text.replace(ch, '')
    # 双引号转义（FTS5 标准：短语内 "" 表示字面量 "）
    text = text.replace('"', '""')
    return text


# FTS 查询停用词：单字虚词/高频通用词，命中不具区分度，反而稀释 BM25 权重。
# 背景：原 to_fts_query 直接输出所有分词项，导致「是/多少/层/T/的」等单字噪声项
#   进入 FTS MATCH，在大文档上千条无差别命中，把核心实义词的重量稀释。
#   这里过滤后，让 BM25 只保留有区分度的实义词，改善排序准确性。
_FTS_QUERY_STOPWORDS = {
    # 单字虚词/助词/判断词
    "是", "的", "了", "在", "有", "和", "与", "及", "或", "等", "为", "被", "把",
    "对", "向", "从", "到", "着", "过", "让", "就", "都", "也", "还", "又",
    "吗", "呢", "吧", "啊", "呀", "么", "呢", "这", "那", "哪", "我", "你",
    "他", "她", "它", "们", "多", "少", "各", "每", "某", "一", "几",
    # 单字度量/量词（不具检索区分度）
    "层", "个", "只", "件", "台", "套", "批", "米", "克", "升",
    # 英文单字母/超短 token（unicode61 会把 GB/T 拆成 GB+T，T 无意义）
    "t", "g", "b", "m", "s", "a", "n",
    # 问询功能词（BM25 无需参与，语义/意图由路由层处理）
    "多少", "怎么", "如何", "哪里", "什么", "为什么", "哪些", "是否", "为啥",
}


def to_fts_query(text: str) -> str:
    """查询用：jieba 词 → FTS5 MATCH 短语查询（OR 连接）

    停用词过滤：过滤单字虚词/高频通用词，避免噪声项稀释 BM25 权重。
    注意：过滤后若无有效词，回退整句短语（保证非空查询不报错）。
    """
    if not _use_jieba():
        return _bigram_query(text)
    words = [w for w in segment(text) if w.lower() not in _FTS_QUERY_STOPWORDS]
    if not words:
        words = segment(text)  # 全停用词时回退原分词，保底非空
    if not words:
        return f'"{_sanitize_fts_term(text)}"'
    # 每个词用双引号短语包裹（unicode61 下多字中文词需整体匹配），OR 连接
    phrases = [f'"{_sanitize_fts_term(w)}"' for w in words]
    # 也加入完整原词作为兜底（若原词非停用词）
    if text.lower() not in _FTS_QUERY_STOPWORDS and text not in words:
        phrases.append(f'"{_sanitize_fts_term(text)}"')
    return " OR ".join(phrases)


# === 旧 bigram 实现（回退用）===

def _bigram_query(text: str) -> str:
    tokens = [f'"{_sanitize_fts_term(text)}"']
    i = 0
    while i < len(text) - 1:
        c1, c2 = text[i], text[i + 1]
        if '\u4e00' <= c1 <= '\u9fff' and '\u4e00' <= c2 <= '\u9fff':
            tokens.append(f'"{_sanitize_fts_term(c1 + c2)}"')
            i += 1
        else:
            i += 1
    return " OR ".join(tokens)
