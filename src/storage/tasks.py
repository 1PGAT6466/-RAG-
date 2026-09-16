"""
入库任务持久化（P20 增强：重试 + 断点续跑 + 死信队列）

设计决策：
  - retry_count/max_retries/next_retry_at 控制指数退避重试
  - dead_letter=1 标记耗尽重试的永久失败任务（死信队列，可查不可重试）
  - checkpoint_stage/checkpoint_data 记录断点位置，重启后续跑而非从头来
  - 瞬时错误（LLM超时/网络抖动）自动重试 ≤3 次，指数退避 5s/10s/20s
  - 持久错误（解析失败/文件不存在）不重试，直接标记 failed
"""
import json
import logging
import time

from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.tasks")

# 重试参数
MAX_RETRIES = 3          # 默认最大重试次数
RETRY_BASE_DELAY = 5.0   # 首次重试延迟（秒）
RETRY_BACKOFF = 2.0      # 退避倍数


def save_task(task: dict):
    """持久化任务状态（upsert），包含重试字段"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tasks (task_id, filename, filepath, status, stage, progress,
           progress_text, chunks, category, file_id, error, created_at,
           retry_count, max_retries, next_retry_at, dead_letter,
           checkpoint_stage, checkpoint_data)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             status=excluded.status, stage=excluded.stage, progress=excluded.progress,
             progress_text=excluded.progress_text, chunks=excluded.chunks,
             category=excluded.category, file_id=excluded.file_id, error=excluded.error,
             retry_count=excluded.retry_count, max_retries=excluded.max_retries,
             next_retry_at=excluded.next_retry_at, dead_letter=excluded.dead_letter,
             checkpoint_stage=excluded.checkpoint_stage,
             checkpoint_data=excluded.checkpoint_data
        """,
        (
            task["task_id"], task["filename"], task.get("filepath", ""),
            task.get("status", "pending"), task.get("stage", ""),
            task.get("progress", 0), task.get("progress_text", ""),
            task.get("chunks", 0), task.get("category", ""),
            task.get("file_id"), task.get("error"),
            task.get("created_at", 0),
            task.get("retry_count", 0), task.get("max_retries", MAX_RETRIES),
            task.get("next_retry_at", 0), task.get("dead_letter", 0),
            task.get("checkpoint_stage", ""),
            json.dumps(task.get("checkpoint_data", {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def load_tasks() -> list[dict]:
    """加载所有持久化任务（供引擎启动时恢复内存状态）"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 解析 checkpoint_data JSON
        try:
            d["checkpoint_data"] = json.loads(d.get("checkpoint_data") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["checkpoint_data"] = {}
        result.append(d)
    return result


def load_task(task_id: str) -> dict | None:
    """W1: 加载单个任务（供 get_status 内存回退查询）"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["checkpoint_data"] = json.loads(d.get("checkpoint_data") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["checkpoint_data"] = {}
    return d


def mark_stale_tasks_failed():
    """启动时：把 running/pending 任务标记为可恢复（而非直接 failed）。

    P20 改造：不再无条件标 failed，而是标为 pending + 设 next_retry_at=0，
    让 recover_tasks 决定是否立即重试（有 checkpoint 的续跑，无 checkpoint 的从头来）。
    仅对 dead_letter=0 的任务生效（死信不恢复）。
    """
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status='pending', progress_text='服务重启，等待恢复',"
        " error='服务重启中断'"
        " WHERE status IN ('running', 'pending') AND dead_letter=0"
    )
    conn.commit()


def load_retryable_tasks() -> list[dict]:
    """加载需要重试的任务：status=retrying 且 next_retry_at <= now"""
    conn = _get_conn()
    now = time.time()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status='retrying' AND next_retry_at <= ? AND dead_letter=0"
        " ORDER BY next_retry_at",
        (now,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["checkpoint_data"] = json.loads(d.get("checkpoint_data") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["checkpoint_data"] = {}
        result.append(d)
    return result


def load_resumable_tasks() -> list[dict]:
    """加载可续跑的任务：status=pending 且有 checkpoint_stage（重启恢复用）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status='pending' AND dead_letter=0"
        " AND checkpoint_stage != '' ORDER BY created_at"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["checkpoint_data"] = json.loads(d.get("checkpoint_data") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["checkpoint_data"] = {}
        result.append(d)
    return result


def mark_task_retrying(task_id: str, retry_count: int, error: str, next_retry_at: float):
    """标记任务为 retrying（等待下次重试）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status='retrying', retry_count=?, error=?, next_retry_at=?"
        " WHERE task_id=?",
        (retry_count, error, next_retry_at, task_id),
    )
    conn.commit()


def mark_task_dead(task_id: str, error: str):
    """标记任务进入死信队列（耗尽重试次数，永久失败）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status='failed', dead_letter=1, error=? WHERE task_id=?",
        (error, task_id),
    )
    conn.commit()


def save_checkpoint(task_id: str, stage: str, data: dict):
    """保存断点信息（某 Stage 完成后调用，重启可从该 Stage 续跑）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET checkpoint_stage=?, checkpoint_data=? WHERE task_id=?",
        (stage, json.dumps(data, ensure_ascii=False), task_id),
    )
    conn.commit()


def delete_task(task_id: str):
    """删除任务记录（内存淘汰时同步清理 DB）"""
    conn = _get_conn()
    conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
    conn.commit()


def cancel_task(task_id: str) -> bool:
    """标记任务为 cancelled（供前端取消按钮用）"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE tasks SET status='cancelled', progress_text='用户取消'"
        " WHERE task_id=? AND status IN ('pending','running','retrying')",
        (task_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def get_dead_letter_tasks() -> list[dict]:
    """获取死信队列（供前端展示永久失败任务）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE dead_letter=1 ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def retry_dead_letter(task_id: str) -> bool:
    """手动重试死信任务（重置重试计数，回到 pending）"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE tasks SET status='pending', dead_letter=0, retry_count=0,"
        " error=NULL, next_retry_at=0, progress_text='手动重试'"
        " WHERE task_id=? AND dead_letter=1",
        (task_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def cleanup_stale_tasks(max_age_hours: int = 24, keep_failed: int = 20):
    """清理脏数据：删除超过 max_age_hours 的已完成/失败任务，只保留最近 keep_failed 条失败记录。"""
    import time as _time
    conn = _get_conn()
    cutoff = _time.time() - max_age_hours * 3600
    conn.execute("DELETE FROM tasks WHERE status='done' AND created_at < ?", (cutoff,))
    failed_rows = conn.execute(
        "SELECT task_id FROM tasks WHERE status='failed' AND created_at < ? ORDER BY created_at DESC",
        (cutoff,),
    ).fetchall()
    if len(failed_rows) > keep_failed:
        to_delete = [r['task_id'] for r in failed_rows[keep_failed:]]
        conn.executemany("DELETE FROM tasks WHERE task_id=?", [(tid,) for tid in to_delete])
    conn.execute("DELETE FROM tasks WHERE status='cancelled'")
    conn.commit()
