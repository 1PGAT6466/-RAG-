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


def to_fts_query(text: str) -> str:
    """查询用：jieba 词 → FTS5 MATCH 短语查询（OR 连接）"""
    if not _use_jieba():
        return _bigram_query(text)
    words = segment(text)
    if not words:
        return f'"{text}"'
    # 每个词用双引号短语包裹（unicode61 下多字中文词需整体匹配），OR 连接
    phrases = [f'"{w}"' for w in words]
    # 也加入完整原词作为兜底
    if text not in words:
        phrases.append(f'"{text}"')
    return " OR ".join(phrases)


# === 旧 bigram 实现（回退用）===

def _bigram_query(text: str) -> str:
    tokens = [f'"{text}"']
    i = 0
    while i < len(text) - 1:
        c1, c2 = text[i], text[i + 1]
        if '\u4e00' <= c1 <= '\u9fff' and '\u4e00' <= c2 <= '\u9fff':
            tokens.append(f'"{c1}{c2}"')
            i += 1
        else:
            i += 1
    return " OR ".join(tokens)
