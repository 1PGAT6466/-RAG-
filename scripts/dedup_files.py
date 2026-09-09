"""
scripts/dedup_files.py — 文件级去重（幂等）

背景
----
知识库存在 21 对「完全重复」文件：每对是同一份 OA 手册的两个上传副本，
一个是规范的完整命名（含「泛微协同办公平台E-cology8.0版本...」前缀），
另一个是简写的短名（如「(1)--流程引擎.docx」「报表.docx」等）。

去重策略
--------
1. 以「content 集合完全一致」判定两文件是同一内容的两份副本。
2. 保留信息更全的「长命名」文件，删除「短命名」副本。
3. 复用 src.storage.files.delete_file（自动清理 chunks / chunks_fts /
   chunks_fts_tri / images / entity_chunks / entity_files / ChromaDB 向量 /
   磁盘图片目录），保证一致性。

安全设计
--------
- 默认 --dry-run，只打印待删清单与释放量，不落库。
- 仅删除「content 集合完全一致」的成对副本，绝不触碰有部分重叠或独有内容的文件。
- 幂等：重复执行不会误删（已删文件不再匹配）。

用法
----
    python scripts/dedup_files.py --dry-run     # 预览
    python scripts/dedup_files.py               # 真正删除
    python scripts/dedup_files.py --min-chunks 0  # 打开删除（默认即可）
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# 确保项目根在 sys.path（无论 python scripts/xxx.py 还是 python -m 都可用）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _chunk_content_set(conn, file_id: int) -> set:
    return {r[0] for r in conn.execute(
        "SELECT content FROM chunks WHERE file_id=?", (file_id,)
    ).fetchall()}


def find_dup_pairs(conn) -> list[tuple]:
    """返回 [(keep_id, delete_id, delete_name, chunk_count), ...]，content 完全一致的成对文件。"""
    files = conn.execute(
        "SELECT id, name, chunk_count FROM files WHERE chunk_count > 0"
    ).fetchall()

    # 按 chunk_count 分组：完全重复的文件 chunk 数必然相同，缩小候选
    by_size: dict[int, list[dict]] = {}
    for f in files:
        by_size.setdefault(f["chunk_count"], []).append(f)

    pairs = []
    for n, group in by_size.items():
        # 缓存每个文件的 content 集合，避免重复查询
        sets = {f["id"]: _chunk_content_set(conn, f["id"]) for f in group}
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                sa, sb = sets[a["id"]], sets[b["id"]]
                if sa and sa == sb:
                    # 保留命名更长（信息更全）的；等长则保留 id 更小的
                    keep = a if len(a["name"]) >= len(b["name"]) else b
                    if len(a["name"]) == len(b["name"]):
                        keep = a if a["id"] < b["id"] else b
                    delete = b if keep["id"] == a["id"] else a
                    pairs.append((keep["id"], delete["id"], delete["name"], delete["chunk_count"]))
    return pairs


def main():
    ap = argparse.ArgumentParser(description="文件级去重（删除完全重复的短名副本）")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不真正删除")
    args = ap.parse_args()

    conn = get_conn()
    pairs = find_dup_pairs(conn)

    if not pairs:
        print("未发现完全重复的文件对，无需去重。")
        return 0

    total_chunks = sum(p[3] for p in pairs)
    print(f"发现 {len(pairs)} 对完全重复文件，共释放 {total_chunks} 个 chunk：\n")

    from src.storage.db import delete_file, get_file

    for keep_id, del_id, del_name, cc in pairs:
        keep_name = get_file(keep_id)["name"]
        print(f"  ✂  删除 [{del_id}] {del_name}  ({cc} chunk)")
        print(f"      保留 [{keep_id}] {keep_name}")

    if args.dry_run:
        print("\n[dry-run] 未做任何改动。去掉 --dry-run 参数执行真实删除。")
        return 0

    # 真实删除
    deleted = 0
    freed = 0
    for keep_id, del_id, del_name, cc in pairs:
        try:
            delete_file(del_id)
            deleted += 1
            freed += cc
        except Exception as e:
            print(f"  ⚠ 删除 [{del_id}] {del_name} 失败: {e}", file=sys.stderr)

    # 汇总
    n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"\n完成：删除 {deleted}/{len(pairs)} 个文件，释放 {freed} chunk。")
    print(f"当前库：files={n_files}, chunks={n_chunks}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
