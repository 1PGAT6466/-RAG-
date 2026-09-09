"""分类词典测试 — src/classification.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classification import classify_text, is_manual, detect_system_folder, MANUAL_EXCLUDE_KEYWORDS


class TestClassifyText:
    def test_connector(self):
        assert classify_text("FAKRA连接器技术规范") == "连接器"

    def test_material(self):
        assert classify_text("LCP材料性能对比") == "材料选型"

    def test_standard(self):
        assert classify_text("GB/T 3077 合金结构钢标准") == "标准件"

    def test_process(self):
        assert classify_text("装配工艺规程SOP") == "工艺规程"

    def test_mechanical_design(self):
        assert classify_text("齿轮传动设计手册") == "机械设计"

    def test_quality(self):
        assert classify_text("SPC过程能力分析报告") == "品质管理"

    def test_electrical(self):
        assert classify_text("PLC伺服控制系统接线图") == "电气自动化"

    def test_external_parts(self):
        assert classify_text("米思米供应商选型目录") == "外购件选型"

    def test_unknown(self):
        assert classify_text("今天的天气真好") == "未分类"

    def test_empty(self):
        assert classify_text("") == "未分类"

    def test_manual_priority(self):
        """操作手册优先于工业关键词"""
        assert classify_text("泛微OA系统使用手册") == "操作手册"

    def test_manual_excluded_for_industrial(self):
        """工业手册不应被归为操作手册"""
        assert classify_text("机械设计手册") != "操作手册"
        assert classify_text("技术手册") != "操作手册"


class TestIsManual:
    def test_manual_positive(self):
        assert is_manual("泛微OA操作手册") is True
        assert is_manual("钉钉使用指南") is True

    def test_manual_excluded(self):
        assert is_manual("设计手册") is False
        assert is_manual("工艺手册") is False

    def test_manual_empty(self):
        assert is_manual("") is False


class TestDetectSystemFolder:
    def test_fanwei(self):
        assert detect_system_folder("泛微OA后台维护手册") == "泛微OA"

    def test_dingtalk(self):
        assert detect_system_folder("钉钉管理指南") == "钉钉"

    def test_no_match(self):
        assert detect_system_folder("连接器设计规范") == ""
