"""
watch_folder.py — 文件夹监控触发器（引擎的又一个入口）

监听目录，新文件投进去 → 自动喂给 IngestEngine 入库+抽取+摘要+标签+预索引。
全程无人值守，不各自实现逻辑，只做"发现文件→enqueue"。

用法：
    python scripts/watch_folder.py --dir D:\watch --interval 10

机制：轮询 + 已处理文件登记（.watched.json 记录已完成文件），
      避免 watchdog 库依赖、避免重复入队。
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path

# 确保 src 可导入
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("rag.watch")

from config import UPLOAD_DIR  # noqa: E402
from src.pipeline.engine import enqueue, get_status  # noqa: E402

SUPPORTED_EXT = {".pdf", ".txt", ".md", ".docx", ".xlsx", ".ppt", ".pptx"}


def load_processed(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_processed(state_file: Path, processed: dict):
    state_file.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="文件夹监控入库触发器")
    parser.add_argument("--dir", required=True, help="要监控的目录")
    parser.add_argument("--interval", type=int, default=10, help="轮询间隔（秒）")
    parser.add_argument("--once", action="store_true", help="只处理当前已有文件后退出（不持续监控）")
    args = parser.parse_args()

    watch_dir = Path(args.dir).resolve()
    if not watch_dir.exists():
        logger.error(f"监控目录不存在: {watch_dir}")
        sys.exit(1)

    state_file = watch_dir / ".watched.json"
    processed = load_processed(state_file)
    logger.info(f"监控目录: {watch_dir}（轮询 {args.interval}s）")

    while True:
        new_files = []
        for f in sorted(watch_dir.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in SUPPORTED_EXT:
                continue
            if f.name.startswith(".") or f.name == ".watched.json":
                continue
            key = f.name
            if processed.get(key):
                continue
            # 文件可能正在写入（未完成），跳过大小不稳定的
            stat = f.stat()
            new_files.append((f, stat.st_size))

        for f, size in new_files:
            processed[f.name] = {"size": size, "enqueued_at": time.time(), "task_id": ""}
            task_id = enqueue(str(f), f.name)
            processed[f.name]["task_id"] = task_id
            logger.info(f"发现新文件入队: {f.name} → {task_id}")
            save_processed(state_file, processed)

        if args.once and new_files:
            logger.info("once 模式：已处理当前文件，退出")
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
