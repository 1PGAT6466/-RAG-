"""核心链路集成测试：检索召回层 + 元数据层回归（跑真链路，依赖真实 DB/模型）

锁住本轮元数据层落地 + FAKRA 修复的回归基准，断言：
1. 「FAKRA连接器规格」应命中 Mini-fakra 产线工艺文档，而非采购流水
2. 「镀金层厚度要求」应命中 Foxconn 连接器手册，而非机械设计手册
3. 权威等级（authority）正确进入召回结果
4. 文件名召回兜底（线性导轨选型 → 标准件新表）
5. 精确命中置顶（_recover_exact_match）的离散优先级语义

注：这些是集成测试，依赖 data/rag.db + 本地 embedding 模型已就绪。
若无数据/模型会 skip（不误报失败）。
"""
import asyncio
import pytest
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "data" / "rag.db"

pytestmark = pytest.mark.skipif(
    not _DB.exists(),
    reason="需要真实数据仓库 data/rag.db + 本地 embedding 模型",
)


def _run(coro):
    """在全新事件循环里跑协程。

    不用 `asyncio.get_event_loop()`：Python 3.11 中该 API 会在「当前线程无循环」时
    告警/报错，且当同进程较早的 TestClient（test_e2e_*）关闭了默认循环后，
    会抛 `RuntimeError: There is no current event loop`，导致本文件在全量跑时
    随机失败（单独跑却通过）。新建独立循环与其它测试完全隔离。
    """
    return asyncio.run(coro)


class TestMetaLayerRetrieval:
    """元数据层（doc_kind/authority）对检索精准度的回归测试"""

    def test_fakra_returns_process_doc_not_purchase(self):
        from src.retrieval.search import search
        results = _run(search("FAKRA连接器规格", top_k=5))
        assert results, "应召回结果"
        # 首条应是 Mini-fakra 产线工艺文档（工艺规程，authority=5），而非采购流水
        top = results[0]
        assert "Mini-fakra" in (top.get("file_name") or "")
        assert "采购" not in (top.get("file_name") or "")

    def test_golden_layer_returns_foxconn(self):
        from src.retrieval.search import search
        results = _run(search("镀金层厚度要求", top_k=5))
        assert results
        assert "Foxconn" in (results[0].get("file_name") or "")

    def test_authority_field_present(self):
        from src.retrieval.search import search
        results = _run(search("连接器", top_k=5))
        # 结果应带 authority/doc_kind 元数据（_recover_exact_match 补查）
        for r in results:
            assert "authority" in r, "结果应带权威等级元数据"


class TestFilenameRecallFallback:
    """文件名召回兜底（线性导轨选型 → 标准件新表）"""

    def test_filename_recall_hits_standard_table(self):
        from src.retrieval.search import _filename_recall
        results = _filename_recall("线性导轨选型")
        # 应命中「标准件新表」这类选型目录文件
        names = [r.get("file_name") or "" for r in results]
        assert any("标准件" in n for n in names), f"文件名召回应命中标准件新表, got: {names[:5]}"


class TestExactMatchRecovery:
    """精确命中置顶（_recover_exact_match）离散优先级语义"""

    def test_high_authority_model_match_top(self):
        from src.retrieval.search import _recover_exact_match
        results = [
            {"id": 1, "content": "FAKRA连接器 采购记录 数量100", "file_name": "采购数据.xlsx",
             "file_id": 999001, "score": 0.9, "authority": 1, "doc_kind": "采购流水"},
            {"id": 2, "content": "Mini-Fakra 装配检测工艺流程", "file_name": "产线工艺.xlsx",
             "file_id": 999002, "score": 0.8, "authority": 5, "doc_kind": "工艺规程"},
        ]
        out = _recover_exact_match("FAKRA连接器规格", results)
        # 高权威工艺规程应置顶（priority 3 > 采购流水 priority 2）
        assert out[0]["id"] == 2
        assert out[1]["id"] == 1

    def test_no_exact_model_keeps_order(self):
        from src.retrieval.search import _recover_exact_match
        results = [
            {"id": 1, "content": "普通内容", "file_name": "a.txt", "file_id": 1, "score": 0.9},
            {"id": 2, "content": "普通内容", "file_name": "b.txt", "file_id": 2, "score": 0.8},
        ]
        out = _recover_exact_match("完全无关的查询", results)
        # 无型号命中时保持稳定序（priority 全 0）
        assert [r["id"] for r in out] == [1, 2]
