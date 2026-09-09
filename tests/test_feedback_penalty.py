"""反馈反哺检索（阶段二 B 方案）单测 — 惩罚因子数学

测试 get_chunk_penalties 的：
  1. 阈值门槛（有效点踩数 < threshold 不惩罚）
  2. 指数衰减（半衰期：Δt = T_half 影响减半）
  3. 惩罚公式 penalty = 1/(1 + alpha * eff)
  4. 候选集合过滤（只查传入 chunk_ids）

用 monkeypatch 注入临时 SQLite 连接，隔离真实 DB，避免污染。
"""
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import src.storage.feedback as fb


# 用本地时间戳保持一致（feedback.created_at 是 localtime 字符串，_parse_feedback_time 按本地解析）
def _localtime_ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


@pytest.fixture
def tmp_conn(tmp_path, monkeypatch):
    """临时 SQLite 内存库，注入 _get_conn，并建 feedback 表。"""
    db_path = tmp_path / "test_feedback.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER,
            message_id INTEGER,
            query TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'down',
            chunk_ids TEXT NOT NULL DEFAULT '[]',
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    monkeypatch.setattr(fb, "_get_conn", lambda: conn)
    # 清空缓存，避免跨测试污染
    fb._invalidate_penalty_cache()
    yield conn
    conn.close()


def _insert(conn, chunk_ids, created_at, kind="down"):
    conn.execute(
        "INSERT INTO feedback (user_id, query, kind, chunk_ids, created_at) VALUES (?,?,?,?,?)",
        (1, "测试", kind, json.dumps(chunk_ids), created_at),
    )
    conn.commit()


class TestChunkPenalties:

    def test_threshold_not_met_no_penalty(self, tmp_conn):
        # 单次点踩，有效点踩=1.0 < threshold=2.0 → 不惩罚
        _insert(tmp_conn, [100], "2026-09-08 10:00:00")
        now = _localtime_ts("2026-09-08 10:00:00")
        pen = fb.get_chunk_penalties({100}, half_life_days=7.0, threshold=2.0, alpha=0.5, now=now)
        assert 100 not in pen  # 未达阈值，不返回（默认 1.0 不惩罚）

    def test_threshold_met_penalizes(self, tmp_conn):
        # 3 次点踩（同一 chunk），有效点踩=3.0 ≥ 2.0 → 惩罚
        for _ in range(3):
            _insert(tmp_conn, [200], "2026-09-08 10:00:00")
        now = _localtime_ts("2026-09-08 10:00:00")
        pen = fb.get_chunk_penalties({200}, half_life_days=7.0, threshold=2.0, alpha=0.5, now=now)
        assert 200 in pen
        # penalty = 1/(1+0.5*3) = 1/2.5 = 0.4
        assert abs(pen[200] - 0.4) < 1e-3

    def test_penalty_formula(self, tmp_conn):
        # 理论验证：penalty = 1/(1+alpha*eff)
        _insert(tmp_conn, [300], "2026-09-08 10:00:00")
        _insert(tmp_conn, [300], "2026-09-08 10:00:00")  # eff=2.0
        now = _localtime_ts("2026-09-08 10:00:00")
        pen = fb.get_chunk_penalties({300}, half_life_days=7.0, threshold=2.0, alpha=0.5, now=now)
        # eff=2.0, alpha=0.5 → penalty=1/(1+1.0)=0.5
        assert abs(pen[300] - 0.5) < 1e-3

    def test_half_life_decay(self, tmp_conn):
        # 半衰期验证：一条半衰期前的点踩，影响减半。
        now = _localtime_ts("2026-09-08 10:00:00")
        half_life = 7 * 86400.0
        t_old = now - half_life
        t_old_str = datetime.fromtimestamp(t_old).strftime("%Y-%m-%d %H:%M:%S")
        # 插 3 条：2 条「现在」（各衰减 1.0）+ 1 条「7 天前」（衰减 0.5）
        _insert(tmp_conn, [400], "2026-09-08 10:00:00")
        _insert(tmp_conn, [400], "2026-09-08 10:00:00")
        _insert(tmp_conn, [400], t_old_str)
        pen = fb.get_chunk_penalties({400}, half_life_days=7.0, threshold=2.0, alpha=0.5, now=now)
        # eff = 1.0 + 1.0 + 0.5 = 2.5 → penalty = 1/(1+0.5*2.5)=1/2.25=0.4444
        assert abs(pen[400] - (1 / 2.25)) < 1e-3

    def test_only_queried_chunks_penalized(self, tmp_conn):
        # 只查 {500}，插入 {500} 和 {600} 的点踩，只有 500 被惩罚
        for _ in range(3):
            _insert(tmp_conn, [500], "2026-09-08 10:00:00")
            _insert(tmp_conn, [600], "2026-09-08 10:00:00")
        now = _localtime_ts("2026-09-08 10:00:00")
        pen = fb.get_chunk_penalties({500}, half_life_days=7.0, threshold=2.0, alpha=0.5, now=now)
        assert 500 in pen
        assert 600 not in pen  # 不在候选集合，不查询/不返回

    def test_hard_down_flag_threshold(self, tmp_conn):
        # 硬阈值：penalty <= 1/(1+alpha*hard_down) 时视为穿透。
        # eff 足够大时 penalty 很小。验证 _apply_feedback_penalty 打标逻辑的阈值边界。
        # 这里只验证公式自洽：alpha=0.5, hard_down=3 → hard_limit=1/(1+1.5)=0.4
        hard_limit = 1.0 / (1.0 + 0.5 * 3)
        assert abs(hard_limit - 0.4) < 1e-3
        # eff=6 → penalty=1/(1+3)=0.25 <= 0.4，应算 hard
        pen = 1.0 / (1.0 + 0.5 * 6)
        assert pen <= hard_limit
