"""
迁移脚本：统一分类标准（一次性）

背景：伏羲系统曾有三套互不一致的分类体系：
  1. 文档分类（ingest._auto_classify）—— files.category
  2. 查询分类（ranking._CATEGORY_KW）—— 检索加权
  3. 标准号领域（entity_extractor.STANDARD_CATEGORY_RULES）—— 实体 attributes.category

本轮已统一为：
  - ①②合并到 src/classification.py 单一权威题材词典 CATEGORY_DICT
  - ③重命名为 standard_domain（隔离自文档题材分类），领域词表统一到 STANDARD_DOMAINS

本脚本做存量数据迁移：
  1. files.category 旧名 → 新统一词典名（设计手册→机械设计、测试报告→品质管理）
  2. standard 实体 attributes.category → standard_domain（重跑 build_semantic_edges 补齐）

幂等：可重复执行。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.db import _get_conn


# 旧文档分类名 -> 新统一词典分类名
CATEGORY_MAPPING = {
    "设计手册": "机械设计",
    "测试报告": "品质管理",
}


def migrate_file_categories():
    conn = _get_conn()
    rows = conn.execute("SELECT id, category FROM files").fetchall()
    migrated = 0
    for r in rows:
        old = r["category"]
        new = CATEGORY_MAPPING.get(old, old)
        if new != old:
            conn.execute("UPDATE files SET category=? WHERE id=?", (new, r["id"]))
            migrated += 1
            print(f"  id={r['id']}: {old} -> {new}")
    conn.commit()
    print(f"文件分类迁移完成，共 {migrated} 个")
    return migrated


def backfill_standard_domain():
    from src.extraction import relation_builder
    result = relation_builder.build_semantic_edges()
    print(f"标准领域回填完成: {result}")
    return result


if __name__ == "__main__":
    print("=== 迁移 1：文档分类名统一 ===")
    migrate_file_categories()
    print()
    print("=== 迁移 2：标准实体 standard_domain 回填 ===")
    backfill_standard_domain()
    print()
    print("全部迁移完成")
