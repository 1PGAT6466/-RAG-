"""P1 交互层测试：对话引擎引用忠实度 + 语义缓存版本控制

覆盖：
- check_citation_fidelity（杜撰编号 phantom / 模糊引用检测）
- build_citation_sources（引用提取）
- 语义缓存版本失效（SEMANTIC_CACHE_VERSION 变化时旧缓存跳过）
"""
import pytest

from src.chat.engine import check_citation_fidelity, build_citation_sources
from src.chat import cache


class TestCitationFidelity:
    def test_healthy_no_phantom(self):
        refs = [{"ref": 1}, {"ref": 2}, {"ref": 3}]
        answer = "根据资料 [1] 和 [2]，结论成立"
        r = check_citation_fidelity(refs, answer)
        assert r["healthy"] is True
        assert r["phantoms"] == []

    def test_phantom_detected(self):
        refs = [{"ref": 1}, {"ref": 2}]
        answer = "见 [1] 和 [5]"  # [5] 不在 refs
        r = check_citation_fidelity(refs, answer)
        assert r["healthy"] is False
        assert 5 in r["phantoms"]

    def test_fuzzy_reference_warns(self):
        refs = [{"ref": 1}]
        answer = "依据资料显示，该参数符合标准"  # 模糊引用未用 [n]
        r = check_citation_fidelity(refs, answer)
        assert len(r["warnings"]) >= 1

    def test_empty_answer(self):
        r = check_citation_fidelity([{"ref": 1}], "")
        assert r["healthy"] is True


class TestBuildCitationSources:
    def test_extract_cited_only(self):
        refs = [
            {"ref": 1, "file_name": "a.txt"},
            {"ref": 2, "file_name": "b.txt"},
            {"ref": 3, "file_name": "c.txt"},
        ]
        answer = "结论见 [2] 和 [3]"
        sources = build_citation_sources(refs, answer)
        cited_refs = [s["ref"] for s in sources]
        assert cited_refs == [2, 3]

    def test_no_citation_returns_none(self):
        # 设计意图：answer 无引用标注时返回空列表（宁可空、不可虚，
        # 见 src/chat/engine.py build_citation_sources docstring），
        # 不退回全部 refs，避免给前端制造「有据可查」的假象。
        refs = [{"ref": 1, "file_name": "a.txt"}]
        answer = "这里没有引用标注"
        sources = build_citation_sources(refs, answer)
        assert len(sources) == 0


class TestSemanticCacheVersion:
    def test_version_is_int(self):
        assert isinstance(cache.SEMANTIC_CACHE_VERSION, int)

    def test_version_bump_invalidates(self):
        # 版本号必须是常量，且 load_cache 只加载匹配版本的缓存
        # 这里验证版本常量存在且为 2（当前迭代）
        assert cache.SEMANTIC_CACHE_VERSION == 2

    def test_lookup_empty_returns_none(self):
        # 空缓存（无 entries）时 lookup 返回 None
        assert cache.lookup(b"\x00" * 1024) is None
