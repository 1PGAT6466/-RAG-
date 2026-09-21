"""
反馈操作（对话操作条的赞/踩反馈落库）
====================================
阶段一：只落库记录，不接入检索链。
阶段二（B 方案）：chunk 级 RRF 融合分惩罚 + 指数衰减 + 阈值。
  见 docs/audit/反馈反哺检索排序方案.md，由 search._apply_feedback_penalty 调用。
"""
import json
import math
import time
import threading
import logging
from datetime import datetime

from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.feedback")

# 惩罚缓存：进程级 TTL 缓存 {(chunk_id,): penalty}，反馈增删时失效。
# 反馈写入频率远低于检索频率，缓存避免每次检索全表扫 feedback。
_penalty_cache: dict = {}
_penalty_cache_ts: float = 0.0
_penalty_cache_lock = threading.Lock()
_PENALTY_CACHE_TTL = 60.0  # 秒


def _invalidate_penalty_cache():
    """反馈增删后主动失效惩罚缓存（写路径调用）。"""
    global _penalty_cache_ts
    with _penalty_cache_lock:
        _penalty_cache.clear()
        _penalty_cache_ts = 0.0


def add_feedback(user_id: int, kind: str, query: str = "",
                 chunk_ids: list = None, conversation_id: int = None,
                 message_id: int = None, comment: str = "") -> int:
    """记录一条反馈。kind: 'up' | 'down'。返回反馈 id。

    #22（2026-09-21）：幂等去重——同一 (user_id, kind, query, chunk_ids) 重复提交时
    更新已有行（不新增），避免重复点踩被累加造成排序操纵。
    """
    if kind not in ("up", "down"):
        kind = "down"
    conn = _get_conn()
    cids_json = json.dumps(chunk_ids or [], ensure_ascii=False)
    # 幂等：先查同键记录
    existing = conn.execute(
        "SELECT id FROM feedback WHERE user_id=? AND kind=? AND query=? AND chunk_ids=? "
        "ORDER BY id DESC LIMIT 1",
        (user_id, kind, query, cids_json),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE feedback SET conversation_id=?, message_id=?, comment=?, "
            "created_at=datetime('now') WHERE id=?",
            (conversation_id, message_id, comment, existing["id"]),
        )
        conn.commit()
        _invalidate_penalty_cache()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO feedback (user_id, conversation_id, message_id, query, kind, chunk_ids, comment) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, conversation_id, message_id, query, kind, cids_json, comment),
    )
    conn.commit()
    _invalidate_penalty_cache()
    return cur.lastrowid


def remove_feedback(user_id: int, kind: str, query: str = "",
                    chunk_ids: list = None) -> bool:
    """撤销用户对某 chunk/query 的反馈（点赞/点踩可撤销）。返回是否删除成功。

    匹配口径：同 user_id + kind + query + chunk_ids（用户对同一回答点踩/点赞可取消）。
    """
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM feedback WHERE user_id=? AND kind=? AND query=? AND chunk_ids=?",
        (user_id, kind, query, json.dumps(chunk_ids or [], ensure_ascii=False)),
    )
    conn.commit()
    _invalidate_penalty_cache()
    return cur.rowcount > 0


def _parse_feedback_time(s: str, now: float = None) -> float:
    """把 feedback.created_at（'YYYY-MM-DD HH:MM:SS' UTC）解析成 epoch 秒。

    #2（2026-09-21）：时间戳已统一为 UTC 存储，故按 UTC 解释。
    解析失败返回 now（视为刚发生，不做衰减）——避免乱七八糟的时间戳导致惩罚异常。
    """
    if now is None:
        now = time.time()
    try:
        from datetime import timezone
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return now


def get_chunk_penalties(chunk_ids: set[int], half_life_days: float = 7.0,
                        threshold: float = 2.0, alpha: float = 0.5,
                        now: float = None) -> dict[int, float]:
    """计算候选 chunk 集合的点踩惩罚因子（阶段二 B 方案核心）。

    返回 {chunk_id: penalty}，其中 penalty ∈ (0, 1]。只返回「有效点踩数 ≥ threshold」
    的 chunk 及其惩罚因子；未达阈值的 chunk 不返回（调用方默认按 1.0 处理，即不惩罚）。

    衰减与惩罚公式（见评审文档 2.x）：
      down_effective(cid) = Σ exp(-ln2 · Δt / T_half)，T_half = half_life_days × 86400
      penalty(cid)         = 1 / (1 + alpha · down_effective(cid))

    只查传入的 chunk_ids（候选集合有限），避免检索路径全表扫描。
    带进程级 TTL 缓存（60s），反馈增删主动失效。
    """
    global _penalty_cache_ts
    if now is None:
        now = time.time()
    if not chunk_ids:
        return {}

    # TTL 缓存：命中且未过期则直接返回缓存（命中候选子集）
    with _penalty_cache_lock:
        cache_valid = (now - _penalty_cache_ts) <= _PENALTY_CACHE_TTL and _penalty_cache
    if cache_valid:
        with _penalty_cache_lock:
            cached = {k: v for k, v in _penalty_cache.items() if k in chunk_ids}
        if cached:
            return cached

    # 半衰期（秒）
    half_life = half_life_days * 86400.0
    decay_k = math.log(2) / half_life if half_life > 0 else 0.0

    # 读全部 down 反馈，按 chunk 聚合并做时间衰减
    conn = _get_conn()
    rows = conn.execute(
        "SELECT chunk_ids, created_at FROM feedback WHERE kind='down'"
    ).fetchall()
    effective: dict[int, float] = {}
    for r in rows:
        try:
            ids = json.loads(r["chunk_ids"] or "[]")
        except Exception:
            continue
        ts = _parse_feedback_time(r["created_at"], now)
        weight = math.exp(-decay_k * (now - ts)) if decay_k > 0 else 1.0
        if weight <= 0:
            continue
        for cid in ids:
            if isinstance(cid, bool):
                continue
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            if cid_int not in chunk_ids:
                continue
            effective[cid_int] = effective.get(cid_int, 0.0) + weight

    # 算惩罚因子（仅达阈值的才返回）
    penalties: dict[int, float] = {}
    for cid, eff in effective.items():
        if eff >= threshold:
            penalties[cid] = round(1.0 / (1.0 + alpha * eff), 6)

    # 写缓存（含未达阈值 chunk 的「不惩罚」标记？不存——未在返回里的默认不惩罚）
    with _penalty_cache_lock:
        _penalty_cache.clear()
        _penalty_cache.update(penalties)
        _penalty_cache_ts = now
    return penalties


def chunk_penalty_stats(chunk_ids: list[int] = None, min_down: int = 1) -> dict[int, int]:
    """按 chunk 统计原始点踩次数（不带时间衰减，仅供诊断/阶段一展示）。返回 {chunk_id: down_count}。"""
    conn = _get_conn()
    down_by_chunk: dict[int, int] = {}
    rows = conn.execute("SELECT chunk_ids FROM feedback WHERE kind='down'").fetchall()
    for r in rows:
        try:
            ids = json.loads(r["chunk_ids"] or "[]")
        except Exception:
            continue
        for cid in ids:
            if isinstance(cid, int):
                down_by_chunk[cid] = down_by_chunk.get(cid, 0) + 1
    if chunk_ids is not None:
        down_by_chunk = {k: v for k, v in down_by_chunk.items() if k in set(chunk_ids)}
    return {k: v for k, v in down_by_chunk.items() if v >= min_down}
