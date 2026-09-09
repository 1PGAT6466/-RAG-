"""P1 交互层测试：chat 意图路由 + 查询改写（规则优先）+ 多轮融合

覆盖：classify_intent / classify_complexity / _rule_rewrite / _token_overlap / _resolve_multiturn。
这些是纯规则逻辑（零 LLM），可离线稳定测试。
"""
import asyncio
import pytest

from src.chat.router import (
    classify_intent, classify_complexity,
    _rule_rewrite, _token_overlap, _SYNONYM_EXPANSIONS,
)
from src.chat.orchestrator import _resolve_multiturn


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestClassifyIntent:
    def test_greeting_chat(self):
        assert classify_intent("你好") == "chat"
        assert classify_intent("你是谁") == "chat"

    def test_thanks_chat(self):
        assert classify_intent("谢谢") == "chat"

    def test_weather_web(self):
        assert classify_intent("今天天气怎么样") == "web"

    def test_knowledge_default(self):
        assert classify_intent("连接器接触电阻是多少") == "knowledge"

    def test_technical_query_not_chat(self):
        assert classify_intent("镀金层厚度要求") == "knowledge"


class TestClassifyComplexity:
    def test_simple_definition(self):
        # 短查询默认 medium（走检索），不误判 simple
        assert classify_complexity("你好") == "medium"  # <5 字 → medium 而非 simple

    def test_comparison_complex(self):
        assert classify_complexity("LCP和PA66有什么区别") == "complex"


class TestRuleRewrite:
    def test_synonym_expansion(self):
        # 「防锈」→ 展开同义词，含「防腐蚀」
        result = _rule_rewrite("怎么防锈")
        assert result is not None
        assert "防锈" in result or "防腐蚀" in result

    def test_exact_entity_returns_self(self):
        # 精确实体（含标准号）直接返回原词
        r = _rule_rewrite("GB/T 3077 20Mn2 抗拉强度")
        assert r is not None

    def test_empty_returns_none(self):
        assert _rule_rewrite("") is None
        assert _rule_rewrite(None) is None


class TestTokenOverlap:
    def test_identical_full_overlap(self):
        assert _token_overlap("连接器", "连接器") == pytest.approx(1.0)

    def test_disjoint_zero(self):
        assert _token_overlap("连接器", "轴承") == 0.0

    def test_partial_overlap(self):
        v = _token_overlap("镀金层厚度", "镀层 电镀 厚度")
        assert 0.0 < v < 1.0


class TestResolveMultiturn:
    def test_pronoun_reference(self):
        history = [{"role": "user", "content": "FAKRA连接器的耐压是多少"},
                   {"role": "assistant", "content": "耐压是..."}]
        fused = _resolve_multiturn("那它的绝缘电阻呢", history)
        assert "FAKRA" in fused

    def test_short_followup(self):
        history = [{"role": "user", "content": "直线导轨选型参数"}]
        fused = _resolve_multiturn("供应商", history)
        assert "直线导轨" in fused

    def test_no_history_returns_self(self):
        assert _resolve_multiturn("独立问题", None) == "独立问题"
        assert _resolve_multiturn("独立问题", []) == "独立问题"
