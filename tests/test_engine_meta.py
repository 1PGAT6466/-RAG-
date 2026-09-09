"""P0 核心链路测试：ingest 引擎任务状态机 + 元数据层（doc_kind/authority）

engine.py 是模块级函数 + 全局 _tasks 字典的状态机（非类），
测试直接操作用 _tasks / _emit / _evict_old_tasks / get_status 等真实 API。
元数据层测试覆盖本轮新增的 detect_doc_kind / resolve_authority 纯规则判定。
"""
import time
import pytest

from src.pipeline import engine
from src.classification import (
    detect_doc_kind, resolve_authority, detect_doc_meta,
    DOC_KIND_AUTHORITY,
)


class _DummyThread:
    """拦截 engine.enqueue 的后台线程（测试只验证任务注册，不跑真实入库链）"""
    def __init__(self, *a, **k):
        pass
    def start(self):
        pass


class TestDocMetadataRules:
    """元数据层：文档类型 + 权威等级 纯规则判定（P1 落地）"""

    def test_process_doc_highest_authority(self):
        # 文件名含「工艺/装配/检测」→ 工艺规程(5)，即使 category 是连接器
        kind, auth = detect_doc_meta("Mini-fakra产线装配检测工艺流程.xlsx", "连接器")
        assert kind == "工艺规程"
        assert auth == 5

    def test_technical_manual(self):
        kind, auth = detect_doc_meta("Foxconn_连接器设计手册.ppt", "连接器")
        assert kind == "技术手册"
        assert auth == 4

    def test_purchase_data_lowest(self):
        # 采购流水权威最低
        kind, auth = detect_doc_meta("2026年采购数据_供应商.xlsx", "外购件选型")
        assert kind == "采购流水"
        assert auth == 1

    def test_selection_catalog(self):
        kind, auth = detect_doc_meta("标准件新表.xlsx", "外购件选型")
        assert kind == "选型目录"
        assert auth == 3

    def test_oa_manual(self):
        kind, auth = detect_doc_meta("泛微OA系统使用手册.docx", "操作手册")
        assert kind == "操作手册"
        assert auth == 2

    def test_unknown_fallback(self):
        assert detect_doc_meta("奇怪的文件.txt", "未分类") == ("未分类", 0)

    def test_authority_ordering(self):
        # 权威等级严格单调：工艺规程 > 技术手册 > 选型目录 > 操作手册 > 采购流水
        assert DOC_KIND_AUTHORITY["工艺规程"] > DOC_KIND_AUTHORITY["技术手册"]
        assert DOC_KIND_AUTHORITY["技术手册"] > DOC_KIND_AUTHORITY["选型目录"]
        assert DOC_KIND_AUTHORITY["选型目录"] > DOC_KIND_AUTHORITY["操作手册"]
        assert DOC_KIND_AUTHORITY["操作手册"] > DOC_KIND_AUTHORITY["采购流水"]
        assert DOC_KIND_AUTHORITY["采购流水"] == 1


class TestEngineTaskState:
    """入库任务状态机：pending → running → done/failed"""

    def test_enqueue_returns_valid_task_id(self, tmp_path, monkeypatch):
        f = tmp_path / "test.txt"
        f.write_text("测试文档内容", encoding="utf-8")
        # 拦截后台线程启动，避免触发真实入库链（测试只验证任务注册逻辑）
        monkeypatch.setattr(engine.threading, "Thread", lambda *a, **k: _DummyThread())
        tid = engine.enqueue(str(f), "test.txt")
        try:
            # enqueue 返回合法 task_id，且任务已注册（get_status 可查到，状态 pending）
            assert tid and isinstance(tid, str)
            status = engine.get_status(tid)
            assert status is not None
            assert status["filename"] == "test.txt"
            assert status["status"] == "pending"  # 后台线程被拦截，状态停留在 pending
        finally:
            with engine._lock:
                engine._tasks.pop(tid, None)

    def test_get_status_missing_returns_none(self):
        assert engine.get_status("nonexistent_task_id") is None

    def test_emit_updates_status(self):
        with engine._lock:
            engine._tasks["test_tid"] = {
                "task_id": "test_tid", "filename": "x", "filepath": "y",
                "status": "pending", "stage": "", "progress": 0,
                "progress_text": "", "chunks": 0, "category": "",
                "file_id": None, "summary": None, "tags": [], "error": None,
                "created_at": time.time(),
            }
        try:
            engine._emit("test_tid", "chunk", 50, "分块中")
            status = engine.get_status("test_tid")
            assert status["status"] == "running"
            assert status["stage"] == "chunk"
            assert status["progress"] == 50
        finally:
            with engine._lock:
                engine._tasks.pop("test_tid", None)

    def test_evict_old_tasks_respects_cap(self):
        # 超过 _MAX_TASKS 时，最旧的 done/failed 任务被淘汰，running/pending 保留
        cap = engine._MAX_TASKS
        with engine._lock:
            engine._tasks.clear()
            # 塞满 done 任务
            for i in range(cap + 10):
                engine._tasks[f"done_{i}"] = {
                    "status": "done", "created_at": time.time() - (cap + 10 - i),
                }
            # 一个 running 任务（不应被淘汰）
            engine._tasks["running_keep"] = {
                "status": "running", "created_at": time.time(),
            }
        try:
            engine._evict_old_tasks()
            with engine._lock:
                # running 任务必保留
                assert "running_keep" in engine._tasks
                # 总数不超过 cap + 1（running 额外）
                assert len(engine._tasks) <= cap + 1
        finally:
            with engine._lock:
                engine._tasks.clear()


class TestEngineCriticalStages:
    """关键 vs 非关键 Stage 的降级语义"""

    def test_critical_stage_registered(self):
        # embed / store 是 critical stage（失败即任务中止）
        assert engine._critical_stages.get("embed") is True
        assert engine._critical_stages.get("store") is True

    def test_noncritical_stage_degrades(self):
        # parse / classify 等非 critical，失败降级继续
        assert engine._critical_stages.get("parse") is not True
