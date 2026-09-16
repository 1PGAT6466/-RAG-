"""
Wiki 批量回填脚本
================
对已有文件执行 Wiki 编译，为满足条件的文档生成 Wiki 页面。

用法：
  python scripts/compile_wiki_backfill.py          # 只编译未有 Wiki 的文件
  python scripts/compile_wiki_backfill.py --all    # 强制重新编译所有文件
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    ap = argparse.ArgumentParser(description="Wiki 批量回填")
    ap.add_argument("--all", action="store_true", help="强制重新编译所有文件")
    args = ap.parse_args()

    from src.storage.db import _get_conn
    from src.pipeline.wiki_compiler import compile_page, save_compiled_page

    conn = _get_conn()
    # 已有 Wiki 页面的 source_file_ids
    existing = set()
    for row in conn.execute("SELECT source_file_ids FROM wiki_pages").fetchall():
        import json
        try:
            ids = json.loads(row[0])
            existing.update(ids)
        except Exception:
            pass

    # 所有未删除文件
    files = conn.execute(
        "SELECT id, name FROM files WHERE deleted_at IS NULL"
    ).fetchall()

    compiled = 0
    skipped = 0
    failed = 0

    for f in files:
        fid = f["id"]
        if not args.all and fid in existing:
            skipped += 1
            continue

        print(f"编译中: [{fid}] {f['name']}...", end=" ", flush=True)
        result = compile_page(fid)
        if result:
            page_id = save_compiled_page(result)
            if page_id:
                print(f"✓ page_id={page_id}")
                compiled += 1
            else:
                print("✗ 保存失败")
                failed += 1
        else:
            print("⊘ 不符合条件或编译失败")
            skipped += 1

    print(f"\n{'='*50}")
    print(f"完成: 编译 {compiled}, 跳过 {skipped}, 失败 {failed}")


if __name__ == "__main__":
    main()
