"""
入库任务持久化
"""
import logging

from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.tasks")


def save_task(task: dict):
    """持久化任务状态（upsert）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tasks (task_id, filename, filepath, status, stage, progress,
           progress_text, chunks, category, file_id, error, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             status=excluded.status, stage=excluded.stage, progress=excluded.progress,
             progress_text=excluded.progress_text, chunks=excluded.chunks,
             category=excluded.category, file_id=excluded.file_id, error=excluded.error
        """,
        (
            task["task_id"], task["filename"], task.get("filepath", ""),
            task.get("status", "pending"), task.get("stage", ""),
            task.get("progress", 0), task.get("progress_text", ""),
            task.get("chunks", 0), task.get("category", ""),
            task.get("file_id"), task.get("error"),
            task.get("created_at", 0),
        ),
    )
    conn.commit()


def load_tasks() -> list[dict]:
    """加载所有持久化任务（供引擎启动时恢复内存状态）"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def mark_stale_tasks_failed():
    """启动时把上次中断的 running/pending 任务标记为 failed"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tasks SET status='failed', error='服务重启，任务中断', progress_text='服务重启中断'"
        " WHERE status IN ('running', 'pending')"
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
        "UPDATE tasks SET status='cancelled', progress_text='用户取消' WHERE task_id=? AND status IN ('pending','running')",
        (task_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def cleanup_stale_tasks(max_age_hours: int = 24, keep_failed: int = 20):
    """清理脏数据：删除超过 max_age_hours 的已完成/失败任务，只保留最近 keep_failed 条失败记录。

    今天实测发现 tasks 表有 6 条 test/x 残留 + 多个历史 failed，全部永久留存。
    对标 RAGFlow「任务历史保留窗口」策略。
    """
    import time
    conn = _get_conn()
    cutoff = time.time() - max_age_hours * 3600
    # 删除超龄的已完成任务
    conn.execute("DELETE FROM tasks WHERE status='done' AND created_at < ?", (cutoff,))
    # 超龄失败任务只保留最近 N 条
    failed_rows = conn.execute(
        "SELECT task_id FROM tasks WHERE status='failed' AND created_at < ? ORDER BY created_at DESC",
        (cutoff,)
    ).fetchall()
    if len(failed_rows) > keep_failed:
        to_delete = [r['task_id'] for r in failed_rows[keep_failed:]]
        conn.executemany("DELETE FROM tasks WHERE task_id=?", [(tid,) for tid in to_delete])
    conn.commit()
    # 清理取消的任务（无需保留）
    conn.execute("DELETE FROM tasks WHERE status='cancelled'")
    conn.commit()
