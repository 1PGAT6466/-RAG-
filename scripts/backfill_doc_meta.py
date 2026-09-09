"""
backfill_doc_meta.py — 回填文档类型 + 权威等级（元数据层 P1.5）

为已入库的 files 表批量计算 doc_kind + authority，写入 files.doc_kind / files.authority。
幂等：可重复运行，只更新需要更新的行。

用法：
    python scripts/backfill_doc_meta.py
    python scripts/backfill_doc_meta.py --dry-run   # 只打印即将写入，不改库
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classification import detect_doc_meta
from src.storage.db import _get_conn, update_file_doc_meta


def main():
    dry_run = "--dry-run" in sys.argv
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, category, chunk_count FROM files ORDER BY id"
    ).fetchall()
    print(f"共 {len(rows)} 个文件，开始回填 doc_kind/authority")
    changed = 0
    for r in rows:
        kind, authority = detect_doc_meta(r["name"], r["category"], "")
        # 特判：采购流水（超大 chunk_count 的选型类文件也可能是采购明细）
        if kind == "选型目录" and (r["chunk_count"] or 0) > 1000:
            kind, authority = "采购流水", 1
        cur_kind = r["doc_kind"] if "doc_kind" in r.keys() else ""
        cur_auth = r["authority"] if "authority" in r.keys() else 0
        if cur_kind != kind or cur_auth != authority:
            changed += 1
            flag = "DRY-RUN" if dry_run else "UPDATE"
            print(f"  [{flag}] file_id={r['id']:>3} {kind}({authority})  <-  {r['name'][:40]}")
            if not dry_run:
                update_file_doc_meta(r["id"], kind, authority)
    print(f"\n{'将更新' if dry_run else '已更新'} {changed} 个文件的元数据")
    conn.close()


if __name__ == "__main__":
    main()
