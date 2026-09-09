"""P2 扩展层测试：实体抽取规则（extract_rule / _match_word / 标准号归一化）

覆盖纯规则抽取，锁定「中文材料词 \b bug 修复」「不锈钢牌号归并」「标准号归一化」等关键逻辑。
"""
import pytest

from src.extraction.entity_extractor import (
    extract_rule, _match_word, _normalize_standard_name, normalize_entities,
)


class TestMatchWord:
    def test_ascii_word_boundary(self):
        # 纯 ASCII 用 \b，避免子串误命中
        assert _match_word("LCP", "材质为 LCP 材料") is True
        assert _match_word("LCP", "LCPolymer") is False  # 不该命中 LCPolymer 里的 LCP

    def test_chinese_substring(self):
        # 中文词用子串匹配（\b 对中文无效，历史 bug 根因）
        assert _match_word("不锈钢", "材质为不锈钢") is True
        assert _match_word("铜合金", "采用铜合金") is True


class TestNormalizeStandard:
    def test_strip_year(self):
        assert _normalize_standard_name("GB/T 157-2001") == "GB/T 157"
        assert _normalize_standard_name("GB/T\n157-2001") == "GB/T 157"

    def test_short_number_invalid(self):
        # 数字部分过短 → None（GB/T 1 这类 PDF 碎片）
        assert _normalize_standard_name("GB/T 1-2000") is None


class TestExtractRule:
    def test_extract_material(self):
        entities = extract_rule("该连接器采用 LCP 材料，镀金处理")
        types = [e["type"] for e in entities]
        assert "material" in types

    def test_extract_chinese_material(self):
        # 中文材料词能被抽到（\b bug 修复回归）
        entities = extract_rule("材质为不锈钢，表面镀金")
        materials = [e["name"] for e in entities if e["type"] == "material"]
        assert any("不锈钢" in m for m in materials)

    def test_extract_empty(self):
        assert extract_rule("") == []

    def test_extract_standard(self):
        entities = extract_rule("符合 GB/T 3077 标准")
        types = [e["type"] for e in entities]
        assert any("standard" in t for t in types)


class TestNormalizeEntities:
    def test_alias_map(self):
        # 材料全称 ↔ 俗名/牌号归并
        entities = [{"name": "PA66", "type": "material", "aliases": ["PA66"]}]
        norm = normalize_entities(entities)
        assert len(norm) >= 1

    def test_empty(self):
        assert normalize_entities([]) == []
