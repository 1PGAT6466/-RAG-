"""
wipe_data.py — 完整擦除业务数据（保留认证账号），回出厂状态供重新上传。

清理范围（用户 2026-09-08 确认）：
  - SQLite 业务数据 + 缓存 + 配置：files/chunks/links/entities 及关联、
    两个 FTS 表、images、dms_imports、standard_categories、conversations、
    rerank_cache、semantic_cache、mcp_translations、mcp_servers_cache、tasks
  - 磁盘：ChromaDB 向量（data/chroma）、uploads 原始文件、images 图片目录
保留：
  - users（认证账号 3 个）
  - mcp_servers（插件配置表）

用法：python scripts/wipe_data.py
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import _get_conn, init_db
from config import UPLOAD_DIR, IMAGES_DIR, DATA_DIR

# 清空的表（业务数据 + 缓存 + 配置，不含 users / mcp_servers）
_TABLES = [
    # 实体/关系（先清外键依赖）
    "entity_relations", "entity_chunks", "entity_files",
    # 链接 + 文档
    "links", "images", "dms_imports",
    # FTS（用 delete_all 特殊处理，见下）
    # chunks / files / entities
    "chunks", "files", "entities",
    # 配置/缓存
    "standard_categories", "conversations", "conversation_messages",
    "rerank_cache", "semantic_cache",
    "mcp_translations", "mcp_servers_cache",
    "tasks",
]


def wipe():
    init_db()
    conn = _get_conn()

    # 1. 清业务表
    for t in _TABLES:
        try:
            conn.execute(f"DELETE FROM {t}")
            print(f"  清空 {t}")
        except Exception as e:
            print(f"  清空 {t} 失败: {e}")

    # 2. FTS 双表：用 delete_all 清（FTS5 虚拟表 DELETE 会留空占位，delete_all 彻底删）
    for fts in ["chunks_fts", "chunks_fts_tri"]:
        try:
            conn.execute(f"INSERT INTO {fts}({fts}) VALUES('delete-all')")
            print(f"  清空 FTS {fts}（delete-all）")
        except Exception as e:
            # 回退：普通 DELETE
            try:
                conn.execute(f"DELETE FROM {fts}")
                print(f"  清空 FTS {fts}（DELETE）")
            except Exception as e2:
                print(f"  清空 FTS {fts} 失败: {e2}")

    # 3. 重置自增 id
    for t in ["files", "chunks", "entities", "entity_relations", "links", "images"]:
        try:
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{t}'")
        except Exception:
            pass
    conn.commit()
    print("  ✓ SQLite 业务数据已清空（users 保留）")

    # 4. 清 ChromaDB
    try:
        chroma_dir = Path(DATA_DIR) / "chroma"
        if chroma_dir.exists():
            shutil.rmtree(chroma_dir)
            print(f"  ✓ 清空 ChromaDB: {chroma_dir}")
    except Exception as e:
        print(f"  清空 ChromaDB 失败: {e}")

    # 5. 清 uploads
    try:
        if Path(UPLOAD_DIR).exists():
            for f in Path(UPLOAD_DIR).iterdir():
                if f.is_file():
                    f.unlink()
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
            print(f"  ✓ 清空 uploads: {UPLOAD_DIR}")
    except Exception as e:
        print(f"  清空 uploads 失败: {e}")

    # 6. 清 images
    try:
        if Path(IMAGES_DIR).exists():
            for f in Path(IMAGES_DIR).iterdir():
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
            print(f"  ✓ 清空 images: {IMAGES_DIR}")
    except Exception as e:
        print(f"  清空 images 失败: {e}")

    print("\n=== 擦除完成 ===")


if __name__ == "__main__":
    # #16（2026-09-21）：破坏性脚本保护——必须显式 --yes，且执行前自动备份 rag.db
    import argparse
    import time
    ap = argparse.ArgumentParser(description="完整擦拭业务数据（保留 users / mcp_servers）")
    ap.add_argument("--yes", action="store_true", help="确认执行（必填，防误操作）")
    ap.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    args = ap.parse_args()

    if not args.yes:
        print("⚠️ 这是破坏性操作，将清空所有业务数据（保留用户帐号）。")
        print("   确认无误后请加 --yes 重新执行：python scripts/wipe_data.py --yes")
        sys.exit(2)

    # 自动备份 rag.db
    if not args.no_backup:
        try:
            from config import DB_PATH
            src = Path(DB_PATH)
            if src.exists():
                ts = time.strftime("%Y%m%d_%H%M%S")
                dst = src.parent / f"rag.db.wipe_backup_{ts}"
                import sqlite3 as _sq
                _c = _sq.connect(str(src))
                _c.execute(f"VACUUM INTO '{dst.as_posix()}'")
                _c.close()
                print(f"✅ 已自动备份: {dst}")
            else:
                print("⚠️ 未找到 rag.db，跳过备份")
        except Exception as e:
            print(f"❌ 自动备份失败，已中止（--no-backup 可强制跳过）: {e}")
            sys.exit(3)

    wipe()
