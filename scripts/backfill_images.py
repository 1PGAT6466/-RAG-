"""
一次性脚本：给历史已入库文件补跑图片提取（可读模式 ![[图]] 显示）

历史文件入库时没有图片提取 stage，此脚本扫描 files 表，
对没有 images 记录的文件补跑 extract_images + 入库。
幂等：已有图片记录的文件跳过。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import IMAGES_DIR
from src.storage import db
from src.pipeline.image_extractor import extract_images


def main():
    files = db.list_files()
    print(f"共 {len(files)} 个文件")
    for f in files:
        fid = f["id"]
        name = f["name"]
        path = f["path"]
        existing = db.count_images(fid)
        if existing > 0:
            print(f"  [跳过] {name}（已有 {existing} 张图片）")
            continue
        if not path or not Path(path).exists():
            print(f"  [跳过] {name}（原始文件不存在）")
            continue
        print(f"  [提取] {name} ...")
        try:
            imgs = extract_images(str(path), fid, IMAGES_DIR)
            if imgs:
                n = db.add_images(fid, imgs)
                print(f"    提取 {n} 张图片")
            else:
                print(f"    无图片（或不支持该格式）")
        except Exception as e:
            print(f"    提取失败: {e}")
    print("完成")


if __name__ == "__main__":
    main()
