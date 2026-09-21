"""
reset_data.py — 清空业务数据（保留用户），用于干净重测

清理：files/chunks/links/entities/entity_chunks/entity_files/entity_relations
     chunks_fts、ChromaDB 向量、uploads 目录
保留：users 表（认证）
"""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import _get_conn, init_db
from config import UPLOAD_DIR, DATA_DIR, DB_PATH


def reset():
    init_db()
    conn = _get_conn()

    # 1. 清空业务表（按外键依赖顺序）
    # 注意：chunks_fts 与 chunks_fts_tri 两个 FTS5 虚拟表都必须清空，
    # 否则残留的 trigram rowid 会与新 chunk 的 AUTOINCREMENT 冲突
    # （sqlite3.IntegrityError: constraint failed，见 MEMORY）。
    tables = [
        "entity_relations", "entity_chunks", "entity_files",
        "links", "chunks_fts", "chunks_fts_tri", "chunks", "files", "entities",
    ]
    for t in tables:
        try:
            conn.execute(f"DELETE FROM {t}")
            print(f"  清空 {t}")
        except Exception as e:
            print(f"  清空 {t} 失败: {e}")
    conn.commit()

    # 重置自增 id（可选，让 id 从 1 开始）
    for t in ["files", "chunks", "entities", "entity_relations", "links"]:
        try:
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{t}'")
        except Exception:
            pass
    conn.commit()

    # 2. 清空 ChromaDB 向量
    try:
        chroma_dir = Path(DATA_DIR) / "chroma"
        if chroma_dir.exists():
            shutil.rmtree(chroma_dir)
            print(f"  清空 ChromaDB: {chroma_dir}")
    except Exception as e:
        print(f"  清空 ChromaDB 失败: {e}")

    # 3. 清空 uploads
    try:
        up = Path(UPLOAD_DIR)
        if up.exists():
            for f in up.iterdir():
                if f.is_file():
                    f.unlink()
            print(f"  清空 uploads: {up}")
    except Exception as e:
        print(f"  清空 uploads 失败: {e}")

    print("\n清理完成")


if __name__ == "__main__":
    # #16（2026-09-21）：破坏性脚本保护——必须显式 --yes，且执行前自动备份 rag.db
    import argparse
    import time
    ap = argparse.ArgumentParser(description="重置数据（清空业务表 + Chroma + uploads）")
    ap.add_argument("--yes", action="store_true", help="确认执行（必填，防误操作）")
    ap.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    args = ap.parse_args()

    if not args.yes:
        print("⚠️ 这是破坏性操作，将清空业务数据。确认请输入：python scripts/reset_data.py --yes")
        sys.exit(2)

    if not args.no_backup:
        try:
            from config import DB_PATH
            src = Path(DB_PATH)
            if src.exists():
                ts = time.strftime("%Y%m%d_%H%M%S")
                dst = src.parent / f"rag.db.reset_backup_{ts}"
                import sqlite3 as _sq
                _c = _sq.connect(str(src))
                _c.execute(f"VACUUM INTO '{dst.as_posix()}'")
                _c.close()
                print(f"✅ 已自动备份: {dst}")
        except Exception as e:
            print(f"❌ 自动备份失败，已中止（--no-backup 可跳过）: {e}")
            sys.exit(3)

    reset()
