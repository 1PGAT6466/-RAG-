"""P0 核心链路测试：embedder.py 纯函数 + llm.py 工具函数

纯函数（无网络/无模型加载）：_pack/_unpack、cosine_similarity、SQ8 量化、extract_json、provider chain。
"""
import numpy as np
import pytest

from src.pipeline.embedder import (
    _pack, _unpack, cosine_similarity,
    quantize_sq8, dequantize_sq8, cosine_similarity_sq8,
)
from src.llm import extract_json, _build_provider_chain


class TestPackUnpack:
    def test_roundtrip(self):
        vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        packed = _pack(vec)
        unpacked = _unpack(packed)
        np.testing.assert_allclose(unpacked, vec, rtol=1e-6)

    def test_cosine_identical(self):
        v = _pack(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_cosine_orthogonal(self):
        a = _pack(np.array([1.0, 0.0], dtype=np.float32))
        b = _pack(np.array([0.0, 1.0], dtype=np.float32))
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_cosine_zero_vector(self):
        a = _pack(np.array([0.0, 0.0], dtype=np.float32))
        b = _pack(np.array([1.0, 0.0], dtype=np.float32))
        assert cosine_similarity(a, b) == 0.0


class TestSQ8Quantize:
    def test_quantize_dequantize_roundtrip(self):
        vec = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float32)
        packed = _pack(vec)
        q, vmin, scale = quantize_sq8(packed)
        restored = dequantize_sq8(q, vmin, scale)
        # SQ8 有量化误差，但应接近原文（<2%）
        np.testing.assert_allclose(restored, vec, atol=0.02)

    def test_constant_vector(self):
        vec = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        packed = _pack(vec)
        q, vmin, scale = quantize_sq8(packed)
        assert vmin == 0.5
        assert scale == 1.0

    def test_cosine_sq8_similar(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 0.0], dtype=np.float32)
        pa = _pack(a); pb = _pack(b)
        qa, va, sa = quantize_sq8(pa)
        qb, vb, sb = quantize_sq8(pb)
        sim = cosine_similarity_sq8(qa, va, sa, qb, vb, sb)
        assert sim == pytest.approx(1.0, abs=0.05)


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        text = '```json\n{"summary": "你好", "tags": ["连接器"]}\n```'
        assert extract_json(text) == {"summary": "你好", "tags": ["连接器"]}

    def test_array(self):
        assert extract_json('[1, 2, 3]', expect="array") == [1, 2, 3]

    def test_object_with_wrapping_text(self):
        text = '以下是结果：{"key": "value"} 完成'
        assert extract_json(text, expect="object") == {"key": "value"}

    def test_invalid_returns_none(self):
        assert extract_json("不是JSON") is None

    def test_empty(self):
        assert extract_json("") is None


class TestBuildProviderChain:
    def test_default_order_deepseek_first(self):
        chain = _build_provider_chain(prefer_mimo=False)
        names = [c[0] for c in chain]
        assert names[0] == "deepseek-flash"
        assert "mimo" in names

    def test_mimo_first(self):
        chain = _build_provider_chain(prefer_mimo=True)
        names = [c[0] for c in chain]
        assert names[0] == "mimo"

    def test_chain_has_three_providers(self):
        assert len(_build_provider_chain()) == 3
