"""
LLM 调用链单元测试
覆盖：provider chain 构建、headers 生成、content 提取、截空检测
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


class TestProviderChain:
    def test_default_chain_deepseek_first(self):
        from src.llm_client import _build_provider_chain
        chain = _build_provider_chain()
        assert chain[0][0] == "deepseek-flash"
        assert chain[-1][0] == "mimo"

    def test_prefer_mimo_chain(self):
        from src.llm_client import _build_provider_chain
        chain = _build_provider_chain(prefer_mimo=True)
        assert chain[0][0] == "mimo"


class TestProviderHeaders:
    def test_mimo_uses_api_key(self):
        from src.llm_client import _provider_headers
        h = _provider_headers("mimo", "sk-test123")
        assert "api-key" in h
        assert h["api-key"] == "sk-test123"
        assert "Authorization" not in h

    def test_deepseek_uses_bearer(self):
        from src.llm_client import _provider_headers
        h = _provider_headers("deepseek-flash", "sk-test456")
        assert "Authorization" in h
        assert h["Authorization"] == "Bearer sk-test456"
        assert "api-key" not in h

    def test_other_provider_uses_bearer(self):
        from src.llm_client import _provider_headers
        h = _provider_headers("deepseek-pro", "sk-test789")
        assert "Authorization" in h


class TestExtractContent:
    def test_normal_content(self):
        from src.llm_client import _extract_content
        choice = {"message": {"content": "Hello world"}}
        content, reasoning, truncated = _extract_content(choice, "deepseek-flash")
        assert content == "Hello world"
        assert reasoning == ""
        assert truncated is False

    def test_reasoning_model_content(self):
        from src.llm_client import _extract_content
        choice = {"message": {"content": "Answer", "reasoning_content": "Let me think..."}}
        content, reasoning, truncated = _extract_content(choice, "mimo")
        assert content == "Answer"
        assert reasoning == "Let me think..."

    def test_empty_content(self):
        from src.llm_client import _extract_content
        choice = {"message": {"content": ""}}
        content, reasoning, truncated = _extract_content(choice, "deepseek-flash")
        assert content == ""


class TestReasoningModelTokens:
    def test_mimo_gets_extra_budget(self):
        from src.llm_client import _reasoning_model_tokens
        result = _reasoning_model_tokens("mimo", 1024)
        assert result > 1024

    def test_deepseek_normal_budget(self):
        from src.llm_client import _reasoning_model_tokens
        result = _reasoning_model_tokens("deepseek-flash", 1024)
        assert result == 1024
