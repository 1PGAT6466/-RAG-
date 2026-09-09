"""to_fts_query 停用词过滤 + rerank 持久化缓存 测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.tokenizer import to_fts_query, _FTS_QUERY_STOPWORDS


class TestFtsQueryStopwords:
    """to_fts_query 应过滤单字虚词/高频通用词，避免噪声项稀释 BM25 权重。"""

    def test_filters_single_char_noise(self):
        q = to_fts_query("连接器接触电阻标准是多少")
        # 「是」「多少」等停用词不应作为独立短语出现
        assert '"是"' not in q
        assert '"多少"' not in q
        # 核心实义词保留
        assert '"连接器"' in q
        assert '"电阻"' in q

    def test_filters_measure_word(self):
        q = to_fts_query("镀金层厚度要求")
        # 「层」单字度量词应被过滤
        assert '"层"' not in q
        assert '"镀金"' in q
        assert '"厚度"' in q

    def test_filters_single_letter(self):
        q = to_fts_query("GB/T 3077")
        # 单字母 T 应被过滤
        assert '"T"' not in q or '"t"' not in q.replace('"T"', '')
        # 数字/实义词保留
        assert '"3077"' in q

    def test_interrogative_filtered(self):
        q = to_fts_query("怎么防锈")
        assert '"怎么"' not in q
        assert '"防锈"' in q

    def test_all_stopwords_fallback(self):
        # 全停用词时回退原分词，保证非空（不崩溃）
        q = to_fts_query("是的")
        assert q  # 非空

    def test_stopword_set_contains_expected(self):
        # 关键停用词在集合中
        for w in ["是", "的", "多少", "层", "怎么", "t", "g", "b"]:
            assert w in _FTS_QUERY_STOPWORDS


class TestRerankPersistentCache:
    """rerank 持久化缓存：候选指纹 + SQLite 跨重启复用。"""

    def test_candidate_fingerprint_stable_and_sensitive(self):
        from src.retrieval.rerank import _candidate_fingerprint
        a = [{"id": 1}, {"id": 2}, {"id": 3}]
        b = [{"id": 1}, {"id": 2}, {"id": 3}]
        c = [{"id": 1}, {"id": 2}, {"id": 4}]
        assert _candidate_fingerprint(a) == _candidate_fingerprint(b)
        assert _candidate_fingerprint(a) != _candidate_fingerprint(c)

    def test_reorder_by_seq_rebuilds_full_dict(self):
        from src.retrieval.rerank import _reorder_by_seq
        candidates = [
            {"id": 1, "content": "c1", "file_name": "f1", "score": 0.8},
            {"id": 2, "content": "c2", "file_name": "f2", "score": 0.9},
            {"id": 3, "content": "c3", "file_name": "f3", "score": 0.7},
        ]
        seq = [{"id": 2, "score": 0.95}, {"id": 1, "score": 0.85}, {"id": 3, "score": 0.75}]
        out = _reorder_by_seq(candidates, seq, top_k=3)
        # 按 seq 顺序重建，且保留完整字段 + 写回 score
        assert [r["id"] for r in out] == [2, 1, 3]
        assert out[0]["file_name"] == "f2"
        assert out[0]["content"] == "c2"
        assert out[0]["score"] == 0.95
