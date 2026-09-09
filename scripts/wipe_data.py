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
    wipe()
