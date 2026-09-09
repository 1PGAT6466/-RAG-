"""
scripts/rebuild_pdf_ocr.py — 损坏文本层 PDF 整本 OCR 重建（多 GPU 进程 + 断点续跑）

改进（2026-08-17）：
  1. 断点续跑：每页 OCR 结果实时落盘 data/ocr_ckpt/*.jsonl，中断后重跑同一命令自动跳过已完成页
  2. 多进程 GPU 并行：N 个进程各持独立 DML 会话，交错分页榨干显卡（实测见 bench_ocr.py）
  3. OCR 先行：先全部 OCR 完成才清库重建——中途失败/被杀，旧数据不受影响
  4. --det-cap：限制 det 网络输入边长（如 960），进一步提速（轻微精度代价）

用法：
    python scripts/rebuild_pdf_ocr.py --file-id 4                  # 默认 2 GPU 进程, dpi=150
    python scripts/rebuild_pdf_ocr.py --file-id 4 --workers 2 --det-cap 960
    python scripts/rebuild_pdf_ocr.py --file-id 4 --start 0 --end 200   # 指定页区间
    中断后续跑：直接重跑同一命令（自动续）；--fresh 强制全部重做
"""
import argparse
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CKPT_DIR = ROOT / "data" / "ocr_ckpt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rebuild_ocr")


# ============================================================
# OCR worker（独立进程：各自 open PDF + 加载模型 + 写 checkpoint）
# ============================================================

def _build_ocr(use_dml: bool, intra_threads: int, det_cap: int = 0):
    """统一走 src.pipeline.ocr_engine：PP-OCRv6 small 优先，回退 v4 mobile。
    返回 (version, engine)。"""
    import sys
    from pathlib import Path
    BASE = Path(__file__).resolve().parent.parent
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from src.pipeline.ocr_engine import build_ocr
    return build_ocr(det_cap=det_cap)


def _shard_worker(pdf_path, page_indices, dpi, use_dml, intra_threads,
                  det_cap, ckpt_path, wid):
    """独立进程：open 一次 PDF + 加载一次模型，逐页 OCR，每页实时追加写 checkpoint"""
    import fitz
    import numpy as np
    try:
        doc = fitz.open(pdf_path)
        ver, ocr = _build_ocr(use_dml, intra_threads, det_cap)
    except Exception as e:
        print(f"[w{wid}] 初始化失败: {e}", flush=True)
        os._exit(1)

    t0 = time.time()
    n = len(page_indices)
    with open(ckpt_path, "a", encoding="utf-8") as f:
        for i, idx in enumerate(page_indices, 1):
            try:
                if idx >= len(doc):
                    text = ""
                else:
                    page = doc[idx]
                    pix = page.get_pixmap(dpi=dpi)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                        pix.height, pix.width, pix.n)
                    if img.shape[2] == 4:
                        img = img[:, :, :3]
                    from src.pipeline.ocr_engine import ocr_page_text
                    text = ocr_page_text(ocr, img)
            except Exception as e:
                text = f"[[OCR_ERROR page {idx}: {e}]]"
            f.write(json.dumps({"p": idx, "t": text}, ensure_ascii=False) + "\n")
            f.flush()
            if i % 20 == 0 or i == n:
                rate = i / max(time.time() - t0, 1e-6)
                print(f"[w{wid}] {i}/{n} 页  {rate:.2f} 页/秒", flush=True)
    os._exit(0)  # 跳过 DML/ONNX 拆卸，避免 teardown 偶发卡死


# ============================================================
# checkpoint 读写
# ============================================================

def _ckpt_glob(tag: str):
    return sorted(CKPT_DIR.glob(f"{tag}_w*.jsonl"))


def _load_ckpt(tag: str) -> dict:
    """读取所有分片 checkpoint，返回 {page_idx: text}（后写覆盖先写）"""
    done = {}
    for fp in _ckpt_glob(tag):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        done[o["p"]] = o["t"]
                    except Exception:
                        continue
        except FileNotFoundError:
            continue
    return done


# ============================================================
# 多进程 OCR 调度（断点续跑）
# ============================================================

def _ocr_all_pages(pdf_path: str, total: int, workers: int, dpi: int,
                   start: int = 0, end: int = None, intra_threads: int = 1,
                   use_dml: bool = False, det_cap: int = 0,
                   tag: str = "doc", fresh: bool = False) -> list:
    end = total if end is None else min(end, total)
    page_range = list(range(start, end))

    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    if fresh:
        for fp in _ckpt_glob(tag):
            fp.unlink(missing_ok=True)

    done = _load_ckpt(tag)
    results = [""] * total
    for idx in page_range:
        if idx in done:
            results[idx] = done[idx]

    remaining = [i for i in page_range if i not in done]
    logger.info(f"OCR 任务: {len(page_range)} 页 [{start},{end}) | 已完成(续跑) {len(page_range)-len(remaining)} 页"
                f" | 待跑 {len(remaining)} 页 | {workers} 进程 | DPI={dpi} | DML={use_dml} | det_cap={det_cap or 'off'}")
    if not remaining:
        logger.info("全部页已有 checkpoint，直接进入重建")
        return results

    ctx = mp.get_context("spawn")
    shards = [remaining[k::workers] for k in range(workers)]
    procs = []
    t0 = time.time()
    for k, shard in enumerate(shards):
        if not shard:
            continue
        ck = CKPT_DIR / f"{tag}_w{k}.jsonl"
        p = ctx.Process(target=_shard_worker,
                        args=(pdf_path, shard, dpi, use_dml, intra_threads,
                              det_cap, str(ck), k),
                        daemon=False)
        p.start()
        procs.append(p)

    for p in procs:
        p.join()

    # 合并（含 worker 挂掉前已写盘的部分）
    done = _load_ckpt(tag)
    for idx in page_range:
        if idx in done:
            results[idx] = done[idx]
    missing = [i for i in remaining if i not in done]
    if missing:
        logger.error(f"仍有 {len(missing)} 页未完成（worker 异常退出），本次中止，"
                     f"已有进度已保存，重跑同一命令可续")
        sys.exit(1)

    rate = len(remaining) / max(time.time() - t0, 1e-6)
    logger.info(f"OCR 完成: {len(remaining)} 页 / {time.time()-t0:.0f}s = {rate:.2f} 页/秒")
    return results


# ============================================================
# 清理 + 重建
# ============================================================

def _clean_file_data(file_id: int):
    """删除文件的 chunks + FTS + Chroma 向量 + 实体关联（保留 files 记录）"""
    from src.storage import db
    conn = db._get_conn()
    chunk_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM chunks WHERE file_id=?", (file_id,)).fetchall()]

    for cid in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM entity_chunks WHERE chunk_id=?", (cid,))
    conn.execute("DELETE FROM entity_files WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    conn.commit()
    logger.info(f"清理旧数据: {len(chunk_ids)} chunks（保留 file 记录）")

    try:
        from src.storage.chroma_store import delete, _use_chroma
        if _use_chroma():
            for cid in chunk_ids:
                delete(cid)
            logger.info(f"清理 Chroma 向量: {len(chunk_ids)} 条")
    except Exception as e:
        logger.warning(f"Chroma 清理失败（已忽略）: {e}")
    return len(chunk_ids)


def _rebuild(file_id: int, pdf_path: str, workers: int, dpi: int,
             start: int = 0, end: int = None, det_cap: int = 0,
             fresh: bool = False, keep_ckpt: bool = False):
    """OCR 重建主体：先 OCR（可断点），成功后才清库重建——中途失败旧数据不受影响"""
    import fitz
    from src.storage import db
    from src.pipeline.chunker import chunk_text, clean_chunks
    from src.pipeline.embedder import encode

    cpu_count = os.cpu_count() or 16
    intra_threads = max(1, cpu_count // workers)
    use_dml = os.getenv("RAG_OCR_DML", "1") == "1"

    f = db.get_file(file_id)
    if not f:
        logger.error(f"file_id={file_id} 不存在")
        return
    logger.info(f"重建目标: [{file_id}] {f['name']}")

    with fitz.open(pdf_path) as doc:
        total = len(doc)

    # 1. OCR（断点续跑，checkpoint 落盘）——先于清库，保证安全
    tag = f"f{file_id}_dpi{dpi}"
    page_texts = _ocr_all_pages(pdf_path, total, workers, dpi, start, end,
                                intra_threads=intra_threads, use_dml=use_dml,
                                det_cap=det_cap, tag=tag, fresh=fresh)
    text = "\n\n".join(t for t in page_texts if t)
    if not text or len(text.strip()) < 100:
        logger.error("OCR 失败或结果过短，中止（旧数据未动）")
        return
    logger.info(f"OCR 全部完成: {len(text)} 字")

    # 2. 清理旧数据
    _clean_file_data(file_id)

    # 3. 分块 + 清洗（与入库链路的 chunk→clean 两段解耦保持一致）
    chunks = chunk_text(text, source_name=f["name"])
    if not chunks:
        logger.error("分块后无内容")
        return
    chunks = clean_chunks(chunks)
    if not chunks:
        logger.error("清洗后无内容")
        return
    logger.info(f"分块: {len(chunks)} 块")

    # 4. 向量化
    contents = [c["content"] for c in chunks]
    embeddings = encode(contents)
    token_counts = [max(1, len(c) // 2) for c in contents]

    # 5. 重新入库（保留原 file_id）
    batch = [
        (file_id, c["index"], c["content"], token_counts[i], embeddings[i],
         {"heading": c["heading"], "source": c["source"]})
        for i, c in enumerate(chunks)
    ]
    chunk_ids = db.add_chunks_batch(batch)
    db.sync_chunk_count(file_id)

    # 6. Chroma 向量写入
    try:
        from src.storage.chroma_store import add_batch, _use_chroma
        if _use_chroma():
            add_batch([
                (chunk_ids[i], embeddings[i], contents[i], file_id, i)
                for i in range(len(chunk_ids))
            ])
    except Exception as e:
        logger.warning(f"Chroma 写入失败（已忽略）: {e}")

    # 7. 重新抽取实体（规则抽取，快速）
    try:
        from src.extraction.relation_builder import process_file_rule
        process_file_rule(file_id)
        logger.info("实体规则重抽完成")
    except Exception as e:
        logger.warning(f"实体重抽失败（已忽略）: {e}")

    # 8. 清理 checkpoint
    if not keep_ckpt:
        n = 0
        for fp in _ckpt_glob(tag):
            fp.unlink(missing_ok=True)
            n += 1
        if n:
            logger.info(f"清理 checkpoint: {n} 个分片文件")

    logger.info(f"✅ 重建完成: {file_id} {f['name']} → {len(chunks)} chunks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id", type=int)
    ap.add_argument("--path", type=str)
    ap.add_argument("--workers", type=int, default=4, help="GPU 并行进程数（默认 4，实测最优；纯 CPU 建议 1）")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--start", type=int, default=0, help="起始页（0-based）")
    ap.add_argument("--end", type=int, default=None, help="结束页（不含）")
    ap.add_argument("--det-cap", type=int, default=960, help="det 输入最大边长（默认 960=实测提速，0=不限制）")
    ap.add_argument("--fresh", action="store_true", help="忽略已有 checkpoint，全部重做")
    ap.add_argument("--keep-ckpt", action="store_true", help="成功后保留 checkpoint 文件")
    args = ap.parse_args()

    from src.storage import db

    if args.file_id:
        f = db.get_file(args.file_id)
        if not f:
            logger.error(f"file_id={args.file_id} 不存在")
            return
        _rebuild(args.file_id, f["path"], args.workers, args.dpi,
                 args.start, args.end, args.det_cap, args.fresh, args.keep_ckpt)
    elif args.path:
        conn = db._get_conn()
        f = conn.execute("SELECT id, path FROM files WHERE path=?",
                         (os.path.abspath(args.path),)).fetchone()
        if not f:
            logger.error(f"路径未入库: {args.path}")
            return
        _rebuild(f["id"], f["path"], args.workers, args.dpi,
                 args.start, args.end, args.det_cap, args.fresh, args.keep_ckpt)
    else:
        ap.print_help()


if __name__ == "__main__":
    # Windows 下多进程需要 freeze_support
    mp.freeze_support()
    main()
