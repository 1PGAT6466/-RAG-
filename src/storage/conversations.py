"""
对话会话操作
"""
import json
import logging

from src.storage.connection import _get_conn

logger = logging.getLogger("rag.db.conversations")


def create_conversation(user_id: int, title: str = "新对话") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (user_id, title) VALUES (?,?)", (user_id, title)
    )
    conn.commit()
    return cur.lastrowid


def list_conversations(user_id: int) -> list[dict]:
    """返回用户的会话列表（按更新时间倒序），带消息数"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.id, c.title, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS msg_count
           FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: int, user_id: int = None) -> dict | None:
    conn = _get_conn()
    if user_id:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return dict(row) if row else None


def get_conversation_messages(conversation_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        result.append(d)
    return result


def add_conversation_message(conversation_id: int, role: str, content: str,
                             mode: str = "knowledge", sources: list = None) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, role, content, mode, sources) VALUES (?,?,?,?,?)",
        (conversation_id, role, content, mode, json.dumps(sources or [], ensure_ascii=False)),
    )
    conn.execute(
        "UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
        (conversation_id,),
    )
    conn.commit()
    return cur.lastrowid


def update_conversation_title(conversation_id: int, title: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE conversations SET title=?, updated_at=datetime('now') WHERE id=?",
        (title, conversation_id),
    )
    conn.commit()


def delete_conversation(conversation_id: int):
    conn = _get_conn()
    # W8: 显式事务 + 正确删除顺序（feedback 无外键级联，必须先删）
    with conn:
        conn.execute("DELETE FROM feedback WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))


def get_chunk_references(chunk_id: int, limit: int = 50) -> list[dict]:
    """反查：某个 chunk 被哪些对话/查询引用过（供文档详情「被引用」角标）。

    扫描 conversation_messages 里 assistant 消息的 sources JSON，找出含该 chunk_id 的记录，
    返回 [{conversation_id, conv_title, query, message_id, ref, chunk_index, created_at}]。

    说明：sources 是 JSON 字符串数组，每个元素含 chunk_id / file_name / ref / chunk_index。
    数据量小（单用户/小团队），直接扫表即可，无需额外关联表。
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, conversation_id, content, sources, created_at FROM conversation_messages "
        "WHERE role='assistant'"
    ).fetchall()
    out = []
    for r in rows:
        try:
            srcs = json.loads(r["sources"] or "[]")
        except Exception:
            continue
        matched = [s for s in srcs if s.get("chunk_id") == chunk_id]
        if not matched:
            continue
        # 取该 assistant 消息对应的 user 提问（同会话、id 小于它、role=user 的最近一条）
        conv_id = r["conversation_id"]
        conv_title = ""
        query = ""
        c = conn.execute("SELECT title FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if c:
            conv_title = c["title"] or ""
        u = conn.execute(
            "SELECT content FROM conversation_messages "
            "WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
            (conv_id, r["id"]),
        ).fetchone()
        if u:
            query = u["content"] or ""
        for s in matched:
            out.append({
                "conversation_id": conv_id,
                "conv_title": conv_title,
                "query": query,
                "message_id": r["id"],
                "ref": s.get("ref"),
                "chunk_index": s.get("chunk_index"),
                "created_at": r["created_at"],
            })
        if len(out) >= limit:
            break
    return out[:limit]


def get_chunk_ref_counts(chunk_ids: set[int]) -> dict[int, int]:
    """批量统计：给定 chunk id 集合，一次扫描算出每个 chunk 被引用的次数。

    供文档详情「被引用 N 次」角标前置显示（避免前端对每个 chunk N+1 请求）。
    一次扫 conversation_messages.sources，按 chunk_id 聚合计数。
    """
    if not chunk_ids:
        return {}
    conn = _get_conn()
    rows = conn.execute(
        "SELECT sources FROM conversation_messages WHERE role='assistant'"
    ).fetchall()
    counts: dict[int, int] = {}
    for r in rows:
        try:
            srcs = json.loads(r["sources"] or "[]")
        except Exception:
            continue
        for s in srcs:
            cid = s.get("chunk_id")
            if cid in chunk_ids:
                counts[cid] = counts.get(cid, 0) + 1
    return counts
