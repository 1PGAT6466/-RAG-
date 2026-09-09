"""检索融合与排序测试 — src/retrieval/"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval.ranking import (
    classify_query, get_dynamic_alpha, exact_match_boost,
    weighted_rrf_fusion, post_rank,
)


class TestClassifyQuery:
    def test_factual(self):
        assert classify_query("GB/T 3077 是什么材质") == "factual"
        assert classify_query("阻抗参数是多少") == "factual"

    def test_semantic(self):
        assert classify_query("LCP和PA66有什么区别") == "semantic"
        assert classify_query("怎么选连接器") == "semantic"

    def test_action(self):
        assert classify_query("如何安装FAKRA连接器") == "action"
        assert classify_query("装配步骤方法") == "action"

    def test_hybrid_default(self):
        assert classify_query("伏羲") == "hybrid"


class TestDynamicAlpha:
    def test_factual_bm25_heavy(self):
        v_w, b_w = get_dynamic_alpha("阻抗参数规格")
        assert b_w > v_w  # BM25 权重更高

    def test_semantic_vector_heavy(self):
        v_w, b_w = get_dynamic_alpha("区别对比优缺点")
        assert v_w > b_w  # 向量权重更高

    def test_weights_sum_to_one(self):
        for q in ["型号参数", "区别原理", "如何安装", "伏羲"]:
            v_w, b_w = get_dynamic_alpha(q)
            assert abs(v_w + b_w - 1.0) < 0.01


class TestExactMatchBoost:
    def test_boost_on_match(self):
        results = [
            {"id": 1, "content": "MLG12-45 连接器技术规范", "file_name": "MLG12系列手册.pdf", "score": 1.0},
            {"id": 2, "content": "通用连接器概述", "file_name": "overview.pdf", "score": 1.0},
        ]
        boosted = exact_match_boost("MLG12", results)
        assert boosted[0]["id"] == 1  # 精确匹配的排第一

    def test_no_boost_on_no_match(self):
        results = [
            {"id": 1, "content": "某文档", "file_name": "doc.pdf", "score": 2.0},
            {"id": 2, "content": "另一文档", "file_name": "doc2.pdf", "score": 1.0},
        ]
        boosted = exact_match_boost("无关查询", results)
        assert boosted[0]["id"] == 1  # 原顺序不变


class TestWeightedRrfFusion:
    def test_merges_sources(self):
        bm25 = [
            {"id": 1, "content": "a", "file_name": "a.pdf", "score": 5.0},
            {"id": 2, "content": "b", "file_name": "b.pdf", "score": 3.0},
        ]
        vec = [
            {"id": 2, "content": "b", "file_name": "b.pdf", "score": 0.9},
            {"id": 3, "content": "c", "file_name": "c.pdf", "score": 0.8},
        ]
        result = weighted_rrf_fusion(bm25, vec, "测试查询", top_k=10)
        ids = [r["id"] for r in result]
        assert 1 in ids
        assert 2 in ids
        assert 3 in ids

    def test_top_k_limits(self):
        bm25 = [{"id": i, "content": "", "file_name": "", "score": 1.0} for i in range(50)]
        vec = [{"id": i, "content": "", "file_name": "", "score": 1.0} for i in range(50)]
        result = weighted_rrf_fusion(bm25, vec, "测试", top_k=5)
        assert len(result) == 5

    def test_graph_source_included(self):
        bm25 = [{"id": 1, "content": "a", "file_name": "a.pdf", "score": 1.0}]
        vec = []
        graph = [{"id": 2, "content": "b", "file_name": "b.pdf", "score": 3.0}]
        result = weighted_rrf_fusion(bm25, vec, "测试", top_k=10, graph=graph)
        ids = [r["id"] for r in result]
        assert 2 in ids  # 图谱召回的结果被融合进来

    def test_empty_inputs(self):
        result = weighted_rrf_fusion([], [], "测试", top_k=10)
        assert result == []
