"""
backup.py — 伏羲数据备份脚本
=============================

备份内容：data/rag.db（VACUUM INTO 一致性快照）+ data/chroma + data/uploads + data/images
输出：backups/rag-YYYYMMDD-HHMMSS.zip，保留最近 N 份（默认 7）

用法：
  python scripts/backup.py                    # 默认备份到 backups/，保留 7 份
  python scripts/backup.py --keep 14          # 保留 14 份
  python scripts/backup.py --dest D:\backups  # 自定义备份目录
  python scripts/backup.py --verify           # 备份后校验 zip 可打开 + DB 可查询

设计决策：
  - VACUUM INTO 替代文件复制：避免 WAL 未合并导致数据丢失
  - .env 单独备份（脱敏：API key 保留，JWT_SECRET 必须保留否则 token 全失效）
  - 备份后自动校验 zip 完整性 + rag.db 可 SELECT 1
  - 失败时打印明确错误，不静默吞掉
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "rag.db"
CHROMA_DIR = DATA_DIR / "chroma"
UPLOAD_DIR = DATA_DIR / "uploads"
IMAGE_DIR = DATA_DIR / "images"
ENV_FILE = ROOT / ".env"
DEFAULT_BACKUP_DIR = ROOT / "backups"


def get_backup_name() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"rag-{ts}"


def vacuum_db(dest_path: Path):
    """用 VACUUM INTO 创建 SQLite 一致性快照（比文件复制安全）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(f"VACUUM INTO '{dest_path}'")
    conn.close()


def backup_env(zipf: zipfile.ZipFile):
    """备份 .env（不脱敏，保留 API key 和 JWT_SECRET）"""
    if ENV_FILE.exists():
        zipf.write(str(ENV_FILE), ".env")


def backup_dir(zipf: zipfile.ZipFile, dir_path: Path, arc_prefix: str):
    """递归备份目录"""
    if not dir_path.exists():
        return
    for f in sorted(dir_path.rglob("*")):
        if f.is_file():
            arcname = f"{arc_prefix}/{f.relative_to(dir_path)}"
            zipf.write(str(f), arcname)


def verify_backup(zip_path: Path) -> bool:
    """校验备份完整性：zip 可打开 + rag.db 可 SELECT 1"""
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            # 检查 zip 完整性
            bad = zf.testzip()
            if bad is not None:
                print(f"  [!] zip 内文件损坏: {bad}")
                return False
            # 检查 rag.db 是否存在且可查询
            names = zf.namelist()
            db_found = False
            for name in names:
                if name.endswith("rag.db"):
                    # 解压到临时文件验证
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                        tmp.write(zf.read(name))
                        tmp_path = tmp.name
                    try:
                        conn = sqlite3.connect(tmp_path)
                        count = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
                        conn.close()
                        if count > 0:
                            db_found = True
                        else:
                            print(f"  [!] rag.db 查询返回 0 张表")
                            return False
                    finally:
                        os.unlink(tmp_path)
            if not db_found:
                print(f"  [!] zip 中未找到 rag.db")
                return False
        return True
    except Exception as e:
        print(f"  [!] 校验失败: {e}")
        return False


def do_backup(dest_dir: Path = None, keep: int = 7, verify: bool = True) -> Path:
    """执行完整备份流程，返回备份 zip 路径"""
    dest_dir = dest_dir or DEFAULT_BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    name = get_backup_name()
    zip_path = dest_dir / f"{name}.zip"
    tmp_db = dest_dir / f"{name}_rag.db"

    print(f"[备份] 开始: {name}")
    print(f"  数据库: {DB_PATH}")
    print(f"  输出: {zip_path}")

    # Step 1: VACUUM INTO（一致性快照）
    print(f"  [1/4] SQLite VACUUM INTO...")
    try:
        vacuum_db(tmp_db)
        db_size = tmp_db.stat().st_size / (1024 * 1024)
        print(f"  ✓ rag.db 快照: {db_size:.1f} MB")
    except Exception as e:
        print(f"  [!] VACUUM 失败: {e}")
        if tmp_db.exists():
            tmp_db.unlink()
        raise

    # Step 2: 打包 zip
    print(f"  [2/4] 打包 zip...")
    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            # rag.db 快照
            zf.write(str(tmp_db), "data/rag.db")
            # Chroma
            if CHROMA_DIR.exists():
                chroma_count = 0
                for f in CHROMA_DIR.rglob("*"):
                    if f.is_file():
                        zf.write(str(f), f"data/chroma/{f.relative_to(CHROMA_DIR)}")
                        chroma_count += 1
                print(f"  ✓ Chroma: {chroma_count} 个文件")
            # uploads
            if UPLOAD_DIR.exists():
                upload_count = 0
                for f in UPLOAD_DIR.rglob("*"):
                    if f.is_file():
                        zf.write(str(f), f"data/uploads/{f.relative_to(UPLOAD_DIR)}")
                        upload_count += 1
                print(f"  ✓ uploads: {upload_count} 个文件")
            # images
            if IMAGE_DIR.exists():
                img_count = 0
                for f in IMAGE_DIR.rglob("*"):
                    if f.is_file():
                        zf.write(str(f), f"data/images/{f.relative_to(IMAGE_DIR)}")
                        img_count += 1
                print(f"  ✓ images: {img_count} 个文件")
            # .env
            backup_env(zf)
            print(f"  ✓ .env")
    finally:
        if tmp_db.exists():
            tmp_db.unlink()

    zip_size = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [3/4] zip 大小: {zip_size:.1f} MB")

    # Step 3: 校验
    if verify:
        print(f"  [4/4] 校验备份完整性...")
        if verify_backup(zip_path):
            print(f"  ✓ 校验通过")
        else:
            print(f"  [!] 校验失败，备份可能不可用")
            return zip_path
    else:
        print(f"  [4/4] 跳过校验")

    # Step 4: 清理旧备份
    _cleanup_old_backups(dest_dir, keep)

    print(f"[备份] 完成: {zip_path}")
    return zip_path


def _cleanup_old_backups(dest_dir: Path, keep: int):
    """保留最近 N 份备份，删除更早的"""
    backups = sorted(dest_dir.glob("rag-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(backups) <= keep:
        return
    for old in backups[keep:]:
        old.unlink()
        print(f"  [清理] 删除旧备份: {old.name}")


def main():
    parser = argparse.ArgumentParser(description="伏羲数据备份")
    parser.add_argument("--dest", type=str, default=str(DEFAULT_BACKUP_DIR), help="备份目录")
    parser.add_argument("--keep", type=int, default=7, help="保留最近 N 份备份")
    parser.add_argument("--no-verify", action="store_true", help="跳过备份校验")
    args = parser.parse_args()

    try:
        do_backup(
            dest_dir=Path(args.dest),
            keep=args.keep,
            verify=not args.no_verify,
        )
    except Exception as e:
        print(f"\n[备份] 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
