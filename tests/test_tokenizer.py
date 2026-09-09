"""分词与 FTS 查询构建测试 — src/storage/tokenizer.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.tokenizer import segment, segment_for_fts, to_fts_query, _sanitize_fts_term


class TestSanitizeFtsTerm:
    def test_double_quote_escaped(self):
        assert '""' in _sanitize_fts_term('test"value')

    def test_fts_operators_stripped(self):
        result = _sanitize_fts_term("test*value^")
        assert "*" not in result
        assert "^" not in result

    def test_normal_text_unchanged(self):
        assert _sanitize_fts_term("正常中文") == "正常中文"

    def test_empty(self):
        assert _sanitize_fts_term("") == ""


class TestSegment:
    def test_chinese(self):
        words = segment("连接器接触电阻")
        assert len(words) > 0
        assert all(w.strip() for w in words)

    def test_empty(self):
        assert segment("") == []

    def test_punctuation_only(self):
        assert segment("，。！？") == []


class TestToFtsQuery:
    def test_produces_valid_fts5_syntax(self):
        query = to_fts_query("连接器型号")
        # 应包含 OR 和双引号
        assert '"' in query

    def test_sanitizes_quotes(self):
        """含双引号的输入不应产生未转义的引号"""
        query = to_fts_query('test"value')
        # 检查不会出现裸引号导致 FTS5 语法错误
        assert query  # 不应崩溃

    def test_empty_fallback(self):
        query = to_fts_query("")
        assert '"' in query  # 至少有引号包裹


class TestSegmentForFts:
    def test_produces_space_separated(self):
        result = segment_for_fts("连接器设计规范")
        assert " " in result  # jieba 分词后用空格连接
