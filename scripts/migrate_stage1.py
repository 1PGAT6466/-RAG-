"""
migrate_stage1.py — 阶段 1 数据迁移：bigram FTS → jieba FTS，SQLite 向量 → ChromaDB

用法：python scripts/migrate_stage1.py
- 重建 chunks_fts（jieba 分词）
- 将 chunks.embedding 写入 ChromaDB
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("migrate")

from src.storage.db import _get_conn
from src.storage.tokenizer import segment_for_fts
from src.storage.chroma_store import add_batch, reset, count


def rebuild_fts():
    conn = _get_conn()
    rows = conn.execute("SELECT id, content FROM chunks").fetchall()
    # 清空重建 FTS
    conn.execute("DELETE FROM chunks_fts")
    for r in rows:
        conn.execute(
            "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
            (r["id"], segment_for_fts(r["content"])),
        )
    conn.commit()
    logger.info(f"FTS 重建完成: {len(rows)} 条（jieba 分词）")


def rebuild_chroma():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, file_id, chunk_index, content, embedding FROM chunks WHERE embedding IS NOT NULL"
    ).fetchall()
    reset()  # 清空旧 collection
    data = [
        (r["id"], r["embedding"], r["content"], r["file_id"], r["chunk_index"])
        for r in rows
    ]
    add_batch(data)
    logger.info(f"ChromaDB 重建完成: {len(data)} 条向量, 当前 count={count()}")


if __name__ == "__main__":
    rebuild_fts()
    rebuild_chroma()
    logger.info("阶段 1 迁移完成")
