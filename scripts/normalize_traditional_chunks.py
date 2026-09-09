"""
normalize_traditional_chunks.py — 存量 chunk 繁简归一化重建
==================================================
背景：历史入库的数据（尤其 Foxconn 繁体 OCR 文档）78% 含繁体字，
而简体查询无法命中繁体内容，导致检索质量受损（如「镀金层」召回失败）。
语言过滤 language_filter 后来才接入且手工映射表覆盖不全（86 个金属/化学字缺失）。

本脚本对存量 chunk 做：
  1. 繁转简（OpenCC t2s，标准映射，覆盖工业金属/化学/技术术语）
  2. 重建 FTS 双索引（chunks_fts jieba + chunks_fts_tri trigram）
  3. 重建 embedding 向量（content 变更后语义需重编码）
  4. 增量更新 ChromaDB 向量（保证 content 与向量一致）

用法：
  python scripts/normalize_traditional_chunks.py --dry-run      # 预览，不写入
  python scripts/normalize_traditional_chunks.py                 # 实际重建
  python scripts/normalize_traditional_chunks.py --limit 100     # 只处理前 100 个（测试）
"""
import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("rag.normalize_trad")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览转换，不写入数据库")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 chunk（0=全部）")
    ap.add_argument("--skip-embed", action="store_true", help="跳过向量重建（只改文本+FTS）")
    ap.add_argument("--embed-only", action="store_true", help="只重建向量（文本+FTS 已重建过时复用）")
    args = ap.parse_args()

    from src.storage.db import _get_conn
    from src.pipeline.language_filter import normalize_han
    from src.storage.tokenizer import segment_for_fts

    conn = _get_conn()

    if args.embed_only:
        rebuild_embeddings(conn, args.limit)
        conn.close()
        return

    # 取所有 chunk（含繁体字优先，但简化起见先全量）
    where = ""
    if args.limit > 0:
        where = f" LIMIT {args.limit}"
    rows = conn.execute(f"SELECT id, content FROM chunks{where}").fetchall()
    total = len(rows)
    logger.info(f"待处理 chunk 数: {total}")

    changed = 0
    converted_chars = 0
    preview = []  # (id, 原前20, 新前20)

    for r in rows:
        cid = r["id"]
        raw = r["content"] or ""
        new = normalize_han(raw)
        if new == raw:
            continue  # 无变化，跳过
        changed += 1
        converted_chars += sum(1 for a, b in zip(raw, new) if a != b)
        if len(preview) < 10:
            preview.append((cid, raw[:30].replace("\n", " "), new[:30].replace("\n", " ")))

        if not args.dry_run:
            # 更新 content
            conn.execute("UPDATE chunks SET content=? WHERE id=?", (new, cid))
            # 重建 FTS 双索引
            conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            conn.execute("DELETE FROM chunks_fts_tri WHERE rowid=?", (cid,))
            conn.execute("INSERT INTO chunks_fts(rowid, content) VALUES (?,?)", (cid, segment_for_fts(new)))
            conn.execute("INSERT INTO chunks_fts_tri(rowid, content) VALUES (?,?)", (cid, new))

    logger.info(f"繁转简完成: 共 {total} 个 chunk，其中 {changed} 个有变化，转换 {converted_chars} 字符")

    print("\n=== 转换预览（前 10 个变化的 chunk）===")
    for cid, o, n in preview:
        print(f"  [{cid}] {o} → {n}")

    if args.dry_run:
        logger.info("DRY-RUN 模式：未写入数据库")
        conn.close()
        return

    conn.commit()
    logger.info("文本 + FTS 索引已提交")

    # 向量重建
    if args.skip_embed:
        logger.info("跳过向量重建（--skip-embed）")
    else:
        rebuild_embeddings(conn, args.limit)

    conn.close()
    logger.info("全部完成")


def rebuild_embeddings(conn, limit: int):
    """重建 embedding 向量 + 同步 ChromaDB"""
    logger.info("开始重建 embedding 向量…")
    from src.pipeline.embedder import encode
    from src.storage.chroma_store import _use_chroma, add as chroma_add

    # 取所有 chunk（含 id、content、file_id、chunk_index）
    where = f" LIMIT {limit}" if limit > 0 else ""
    rows = conn.execute(
        f"SELECT id, content, file_id, chunk_index FROM chunks WHERE content IS NOT NULL{where}").fetchall()
    total = len(rows)
    logger.info(f"需重编码 {total} 个 chunk（本地 bge-large CPU 约 0.8s/个）")

    BATCH = 8
    for i in range(0, total, BATCH):
        batch = rows[i:i + BATCH]
        ids = [r["id"] for r in batch]
        texts = [r["content"] for r in batch]
        try:
            vecs = encode(texts)  # -> list[bytes]（每元素为 float32 字节）
        except Exception as e:
            logger.error(f"编码批次失败 (i={i}): {e}")
            continue

        for r, vec in zip(batch, vecs):
            cid = r["id"]
            content = r["content"]
            file_id = r["file_id"]
            chunk_index = r["chunk_index"]
            # 写回 SQLite embedding（bytes）
            conn.execute("UPDATE chunks SET embedding=? WHERE id=?", (vec, cid))
            # 同步 ChromaDB（删除旧向量 + 加新向量）
            if _use_chroma():
                try:
                    chroma_add(cid, vec, content, file_id, chunk_index)
                except Exception as e:
                    logger.warning(f"ChromaDB 更新 chunk {cid} 失败: {e}")

        conn.commit()
        if (i // BATCH) % 25 == 0:
            logger.info(f"进度: {min(i + BATCH, total)}/{total}")

    logger.info("向量重建完成")


if __name__ == "__main__":
    main()
