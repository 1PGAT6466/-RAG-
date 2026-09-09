"""
reclassify_manuals.py — 批量修正存量文件的分类与文件夹

背景：泛微 OA 操作手册（28 个模块）被工业题材分类词典误判成
「标准件/外购件选型/品质管理」等零散类别。本脚本按新分类逻辑
（classification.is_manual + detect_system_folder）批量重分类，
并把操作手册按系统归到虚拟文件夹（如 /泛微OA）。

用法：
    python scripts/reclassify_manuals.py [--dry-run]
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage import db
from src.classification import detect_system_folder, MANUAL_CATEGORY
from src.pipeline.ingest import _auto_classify, _auto_folder


def main(dry_run: bool):
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT id, name, category, folder FROM files ORDER BY id"
    ).fetchall()

    # 收集每个文件正文前 2000 字，用于识别裸文件名的系统
    changed = 0
    for file_id, name, old_cat, old_folder in rows:
        # 取正文前 2000 字（多个 chunk 拼接）
        chunks = conn.execute(
            "SELECT content FROM chunks WHERE file_id=? ORDER BY chunk_index LIMIT 20",
            (file_id,),
        ).fetchall()
        head = " ".join(c[0] or "" for c in chunks)[:2000]
        combined = f"{name}"

        # 用「文件名优先」逻辑，与入库 classify stage 保持一致
        new_cat = _auto_classify(name, head)
        new_folder = _auto_folder(name, head)

        # 只对「操作手册」处理文件夹；其他分类不自动建文件夹（保持 folder 不动）
        if new_cat == MANUAL_CATEGORY and new_folder:
            target_folder = "/" + new_folder
        else:
            target_folder = old_folder

        cat_changed = new_cat != old_cat
        folder_changed = target_folder != old_folder

        if not cat_changed and not folder_changed:
            continue

        changed += 1
        if dry_run:
            print(f"[dry-run] id={file_id} {name}")
            print(f"    category: {old_cat} -> {new_cat}")
            print(f"    folder:   {old_folder} -> {target_folder}")
            continue

        if cat_changed:
            db.update_file_category(file_id, new_cat)
        if folder_changed:
            db.update_file_folder(file_id, target_folder)
        print(f"id={file_id} {name}")
        print(f"    category: {old_cat} -> {new_cat}")
        print(f"    folder:   {old_folder} -> {target_folder}")

    print(f"\n共 {len(rows)} 个文件，需修正 {changed} 个" + ("（dry-run，未实际写入）" if dry_run else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只预览不写入")
    args = parser.parse_args()
    main(args.dry_run)
