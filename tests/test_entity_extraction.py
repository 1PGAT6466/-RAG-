"""实体抽取测试 — src/extraction/entity_extractor.py（仅规则抽取，无 LLM）"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extraction.entity_extractor import (
    extract_rule, normalize_entities, _normalize_standard_name,
    classify_standard, is_low_confidence_standard,
)


class TestExtractRule:
    def test_connector_fakra(self):
        entities = extract_rule("该产品采用 FAKRA 连接器")
        names = [e["name"] for e in entities]
        assert "FAKRA" in names
        assert any(e["type"] == "connector" for e in entities if e["name"] == "FAKRA")

    def test_material_lcp(self):
        entities = extract_rule("外壳材料为 LCP，耐高温")
        names = [e["name"] for e in entities]
        assert "LCP" in names
        assert any(e["type"] == "material" for e in entities if e["name"] == "LCP")

    def test_standard_number(self):
        entities = extract_rule("符合 GB/T 3077-2015 标准")
        names = [e["name"] for e in entities]
        assert any("GB/T" in n for n in names)
        assert any(e["type"] == "standard" for e in entities)

    def test_process(self):
        entities = extract_rule("采用电镀工艺处理表面")
        names = [e["name"] for e in entities]
        assert "电镀" in names
        assert any(e["type"] == "process" for e in entities if e["name"] == "电镀")

    def test_param_impedance(self):
        entities = extract_rule("阻抗: 50Ω")
        params = [e for e in entities if e["type"] == "param"]
        assert len(params) >= 1
        assert params[0]["name"] == "阻抗"
        assert params[0]["attributes"]["value"] == "50"

    def test_param_temperature_range(self):
        entities = extract_rule("温度范围: -40~105℃")
        params = [e for e in entities if e["type"] == "param"]
        assert len(params) >= 1
        assert params[0]["attributes"]["value"] == "-40~105"

    def test_empty_text(self):
        assert extract_rule("") == []

    def test_no_entities(self):
        entities = extract_rule("今天天气不错")
        assert len(entities) == 0


class TestNormalizeStandardName:
    def test_strip_year(self):
        assert _normalize_standard_name("GB/T 3077-2015") == "GB/T 3077"

    def test_clean_newline(self):
        assert _normalize_standard_name("GB/T\n157-2001") == "GB/T 157"

    def test_short_number_filtered(self):
        """数字部分过短（< 2 位）应返回 None"""
        assert _normalize_standard_name("GB/T 1") is None

    def test_iso(self):
        result = _normalize_standard_name("ISO 9001:2015")
        assert "ISO" in result
        assert "9001" in result


class TestClassifyStandard:
    def test_material_range(self):
        cat = classify_standard("GB/T 3077")
        assert cat == "材料"

    def test_fastener(self):
        cat = classify_standard("GB/T 93")
        assert cat == "紧固件"

    def test_iec(self):
        cat = classify_standard("IEC 60950")
        assert cat == "电工"

    def test_unknown_prefix(self):
        cat = classify_standard("XX 1234")
        assert cat is None


class TestNormalizeEntities:
    def test_series_merging(self):
        """MLG12-45 和 MLG12-60 应合并为 MLG12 with variants"""
        entities = [
            {"name": "MLG12-45", "type": "connector", "aliases": [], "description": "", "attributes": {}},
            {"name": "MLG12-60", "type": "connector", "aliases": [], "description": "", "attributes": {}},
        ]
        result = normalize_entities(entities)
        names = [e["name"] for e in result]
        assert "MLG12" in names
        mlg12 = next(e for e in result if e["name"] == "MLG12")
        assert "45" in mlg12["attributes"].get("variants", [])
        assert "60" in mlg12["attributes"].get("variants", [])

    def test_generic_stopwords_filtered(self):
        """泛化词应被过滤"""
        entities = [
            {"name": "连接器", "type": "connector", "aliases": [], "description": "", "attributes": {}},
            {"name": "FAKRA", "type": "connector", "aliases": [], "description": "", "attributes": {}},
        ]
        result = normalize_entities(entities)
        names = [e["name"] for e in result]
        assert "连接器" not in names
        assert "FAKRA" in names
