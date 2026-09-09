"""
清理 ChromaDB 孤儿向量
======================

背景：历史上 delete_file 尚未接入 Chroma 清理、或反复重建/重传文件时，
ChromaDB 可能遗留「SQLite chunks 已删除、但向量还在」的孤儿向量。
这些孤儿向量会污染检索（召回已删除文档的旧 chunk，file_name 为空）。

本脚本：把 Chroma 里「不在 SQLite chunks 表中」的向量 id 全部删除。
- 只删孤儿，不碰有效向量（SQLite 存在的 id 一律保留）
- 幂等，可重复执行
- 执行前打印孤儿数量，执行后打印清理结果

用法：
    python scripts/cleanup_chroma_orphans.py [--dry-run]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.chroma_store import _get_collection, _use_chroma
from src.storage.db import _get_conn


def main(dry_run: bool = False):
    if not _use_chroma():
        print("ChromaDB 未启用（RAG_CHROMA != 1），无需清理")
        return

    col = _get_collection()
    chroma_ids = [int(i) for i in col.get(include=[])["ids"]]

    conn = _get_conn()
    sqlite_ids = {r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()}

    orphans = [cid for cid in chroma_ids if cid not in sqlite_ids]
    print(f"Chroma 向量总数: {len(chroma_ids)}")
    print(f"SQLite chunks 总数: {len(sqlite_ids)}")
    print(f"孤儿向量（Chroma 有、SQLite 无）: {len(orphans)} 个")

    if not orphans:
        print("无孤儿向量，无需清理 ✅")
        return

    if dry_run:
        print(f"[dry-run] 将删除 {len(orphans)} 个孤儿向量，示例: {orphans[:10]}")
        return

    # 批量删除（复用 delete_where，一次/分批调用 col.delete，避免逐条删卡死）
    from src.storage.chroma_store import delete_where
    deleted = delete_where([str(cid) for cid in orphans])
    print(f"清理完成，删除 {deleted} 个孤儿向量 ✅")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
