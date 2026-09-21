#!/usr/bin/env python3
"""
health_check.py — 数据一致性守护脚本（可手动跑 / 挂 cron）

检查项（全部只读，零副作用）：
  1. 孤儿 chunk：chunk 的 file_id 在 files 表中不存在
  2. chunk_count 不一致：files.chunk_count 与 chunks 实际行数不符
  3. 孤儿实体关联：entity_chunks 引用的 chunk/entity 不存在
  4. FTS 行数一致性：chunks 表与 chunks_fts 文档数一致
  5. 空 embedding：有内容但无向量（会影响向量检索）
  6. 孤儿 entity_files / entity_relations

退出码：0=全部健康，1=发现漂移（供 cron/CI 判定）
用法：
  python scripts/health_check.py              # 默认 data/rag.db
  python scripts/health_check.py --db <path>  # 指定库
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# 统一标准：本脚本与父进程（ragctl / ops_daily）一律用 UTF-8 交换文本。
# Windows 控制台默认代码页是 GBK，父进程以 utf-8 解码会把中文变成乱码，
# 因此这里强制把 stdout/stderr 重置为 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _check_chunk_count(cur, issues: list):
    """files.chunk_count 与 chunks 实际行数一致性"""
    cur.execute("""
        SELECT f.id, f.name, f.chunk_count,
               (SELECT COUNT(*) FROM chunks c WHERE c.file_id = f.id) AS actual
        FROM files f
    """)
    for fid, name, declared, actual in cur.fetchall():
        if declared != actual:
            issues.append(f"[chunk_count] file_id={fid} '{name}' 声明 {declared} 实际 {actual}")


def _check_orphan_chunks(cur, issues: list):
    """chunk 的 file_id 无对应 file"""
    cur.execute("""
        SELECT COUNT(*) FROM chunks c
        LEFT JOIN files f ON f.id = c.file_id
        WHERE f.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        issues.append(f"[孤儿 chunk] {n} 个 chunk 的 file_id 无对应文件")


def _check_orphan_entity_relations(cur, issues: list):
    """entity_chunks 引用的 chunk/entity 不存在"""
    cur.execute("""
        SELECT COUNT(*) FROM entity_chunks ec
        LEFT JOIN chunks c ON c.id = ec.chunk_id
        LEFT JOIN entities e ON e.id = ec.entity_id
        WHERE c.id IS NULL OR e.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        issues.append(f"[孤儿 entity_chunks] {n} 条关联引用了不存在的 chunk/entity")


def _check_orphan_entity_files(cur, issues: list):
    cur.execute("""
        SELECT COUNT(*) FROM entity_files ef
        LEFT JOIN files f ON f.id = ef.file_id
        LEFT JOIN entities e ON e.id = ef.entity_id
        WHERE f.id IS NULL OR e.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        issues.append(f"[孤儿 entity_files] {n} 条关联引用了不存在的 file/entity")


def _check_orphan_relations(cur, issues: list):
    """entity_relations 的 source/target 实体不存在"""
    cur.execute("""
        SELECT COUNT(*) FROM entity_relations er
        LEFT JOIN entities s ON s.id = er.source_id
        LEFT JOIN entities t ON t.id = er.target_id
        WHERE s.id IS NULL OR t.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        issues.append(f"[孤儿 entity_relations] {n} 条边引用了不存在的实体")


def _check_fts_consistency(cur, issues: list):
    """chunks 与 FTS 文档数一致（FTS5 虚拟表 rowid 对应用 chunk id）"""
    try:
        cur.execute("SELECT COUNT(*) FROM chunks")
        chunk_n = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM chunks_fts")
        fts_n = cur.fetchone()[0]
        if chunk_n != fts_n:
            issues.append(f"[FTS 不一致] chunks={chunk_n} 但 FTS={fts_n}（差 {chunk_n - fts_n}）")
    except sqlite3.OperationalError as e:
        issues.append(f"[FTS 检查失败] {e}")


def _check_empty_embedding(cur, issues: list):
    """有内容但无向量（影响向量检索覆盖）"""
    cur.execute("""
        SELECT COUNT(*) FROM chunks
        WHERE content IS NOT NULL AND length(content) > 0
          AND (embedding IS NULL OR length(embedding) = 0)
    """)
    n = cur.fetchone()[0]
    if n:
        issues.append(f"[空 embedding] {n} 个 chunk 有内容但无向量")


def run(db_path: str) -> list:
    if not Path(db_path).exists():
        return [f"[致命] 数据库不存在: {db_path}"]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    issues = []
    checks = [
        _check_chunk_count, _check_orphan_chunks, _check_orphan_entity_relations,
        _check_orphan_entity_files, _check_orphan_relations,
        _check_fts_consistency, _check_empty_embedding,
    ]
    for fn in checks:
        try:
            fn(cur, issues)
        except Exception as e:
            issues.append(f"[{fn.__name__} 执行异常] {e}")
    conn.close()

    # 汇总统计（供参考）
    total_files = total_chunks = total_entities = 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for t, cnt in [("files", None), ("chunks", None), ("entities", None)]:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            v = cur.fetchone()[0]
            if t == "files":
                total_files = v
            elif t == "chunks":
                total_chunks = v
            else:
                total_entities = v
        conn.close()
    except Exception:
        pass

    header = f"files={total_files} chunks={total_chunks} entities={total_entities}"
    return issues, header


def main():
    ap = argparse.ArgumentParser(description="数据一致性守护")
    ap.add_argument("--db", default=None, help="数据库路径，默认 data/rag.db")
    args = ap.parse_args()

    db_path = args.db or str(Path(__file__).resolve().parent.parent / "data" / "rag.db")
    issues, header = run(db_path)

    print(f"=== 数据一致性检查: {db_path} ===")
    print(f"规模: {header}")
    print()
    if not issues:
        print("✅ 全部健康（7 项检查通过）")
        sys.exit(0)
    else:
        print(f"⚠️ 发现 {len(issues)} 项漂移：")
        for i in issues:
            print(f"  - {i}")
        sys.exit(1)


if __name__ == "__main__":
    main()
