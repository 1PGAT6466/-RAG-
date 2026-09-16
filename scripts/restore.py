"""
restore.py — 伏羲数据恢复脚本
==============================

用法：
  python scripts/restore.py backups/rag-20260910-093500.zip

恢复流程：
  1. 停服提示（用户需先手动停止服务）
  2. 解压备份到临时目录
  3. 校验 rag.db 完整性
  4. 覆盖 data/ 目录（rag.db + chroma + uploads + images）
  5. 恢复 .env（可选，默认跳过）
  6. 启动服务验证

安全措施：
  - 恢复前自动备份当前 data/ 到 backups/pre-restore-*.zip（回滚点）
  - 覆盖前确认（--force 跳过确认）
  - 恢复后自动跑 SQLite 完整性检查
"""
import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "rag.db"
CHROMA_DIR = DATA_DIR / "chroma"
UPLOAD_DIR = DATA_DIR / "uploads"
IMAGE_DIR = DATA_DIR / "images"
ENV_FILE = ROOT / ".env"
BACKUP_DIR = ROOT / "backups"


def check_zip(zip_path: Path) -> dict:
    """检查备份 zip 内容"""
    info = {"files": 0, "has_db": False, "has_chroma": False, "has_uploads": False}
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        names = zf.namelist()
        info["files"] = len(names)
        for name in names:
            if name.endswith("rag.db"):
                info["has_db"] = True
            elif name.startswith("data/chroma/"):
                info["has_chroma"] = True
            elif name.startswith("data/uploads/"):
                info["has_uploads"] = True
    return info


def verify_db_integrity(db_bytes: bytes) -> bool:
    """验证数据库完整性"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(db_bytes)
        tmp_path = tmp.name
    try:
        conn = sqlite3.connect(tmp_path)
        # integrity_check
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        return result == "ok"
    finally:
        os.unlink(tmp_path)


def pre_restore_backup() -> Path:
    """恢复前自动备份当前数据（回滚点）"""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = BACKUP_DIR / f"pre-restore-{ts}.zip"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[恢复] 创建回滚点: {zip_path.name}")
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_PATH.exists():
            # VACUUM INTO 快照
            tmp_db = BACKUP_DIR / f"_tmp_restore_{ts}.db"
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(f"VACUUM INTO '{tmp_db}'")
                conn.close()
                zf.write(str(tmp_db), "data/rag.db")
            finally:
                if tmp_db.exists():
                    tmp_db.unlink()
        for dir_path, prefix in [(CHROMA_DIR, "data/chroma"), (UPLOAD_DIR, "data/uploads"), (IMAGE_DIR, "data/images")]:
            if dir_path.exists():
                for f in dir_path.rglob("*"):
                    if f.is_file():
                        zf.write(str(f), f"{prefix}/{f.relative_to(dir_path)}")
        if ENV_FILE.exists():
            zf.write(str(ENV_FILE), ".env")

    print(f"  ✓ 回滚点已创建")
    return zip_path


def do_restore(zip_path: Path, force: bool = False, restore_env: bool = False):
    """执行恢复流程"""
    if not zip_path.exists():
        print(f"[恢复] 错误: 备份文件不存在: {zip_path}")
        sys.exit(1)

    print(f"[恢复] 备份文件: {zip_path}")
    info = check_zip(zip_path)
    print(f"  包含 {info['files']} 个文件")
    print(f"  rag.db: {'✓' if info['has_db'] else '✗'}")
    print(f"  chroma: {'✓' if info['has_chroma'] else '✗'}")
    print(f"  uploads: {'✓' if info['has_uploads'] else '✗'}")

    if not info["has_db"]:
        print(f"[恢复] 错误: 备份中没有 rag.db，无法恢复")
        sys.exit(1)

    if not force:
        print(f"\n[恢复] ⚠️  即将覆盖当前 data/ 目录！")
        print(f"  数据库: {DB_PATH}")
        print(f"  向量库: {CHROMA_DIR}")
        print(f"  上传文件: {UPLOAD_DIR}")
        confirm = input("  确认恢复？(y/N): ").strip().lower()
        if confirm != "y":
            print("[恢复] 已取消")
            sys.exit(0)

    # Step 1: 创建回滚点
    pre_restore_backup()

    # Step 2: 解压到临时目录
    print(f"[恢复] 解压备份...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(tmp_path))

        # Step 3: 校验 rag.db
        print(f"[恢复] 校验数据库完整性...")
        db_file = tmp_path / "data" / "rag.db"
        if not db_file.exists():
            print(f"[恢复] 错误: 解压后未找到 data/rag.db")
            sys.exit(1)

        with open(db_file, "rb") as f:
            db_bytes = f.read()
        if not verify_db_integrity(db_bytes):
            print(f"[恢复] 错误: 数据库完整性检查失败")
            sys.exit(1)
        print(f"  ✓ 数据库完整性检查通过")

        # Step 4: 停服提示
        print(f"\n[恢复] ⚠️  请先停止伏羲服务！")
        input("  按 Enter 继续（已确认服务已停止）...")

        # Step 5: 覆盖数据
        print(f"[恢复] 覆盖数据...")

        # rag.db
        shutil.copy2(str(db_file), str(DB_PATH))
        print(f"  ✓ rag.db")

        # WAL/SHM 清理（恢复后用新 DB，旧的 WAL 不兼容）
        for suffix in ["-wal", "-shm"]:
            wal = Path(str(DB_PATH) + suffix)
            if wal.exists():
                wal.unlink()
                print(f"  ✓ 清理 {wal.name}")

        # chroma
        chroma_src = tmp_path / "data" / "chroma"
        if chroma_src.exists():
            if CHROMA_DIR.exists():
                shutil.rmtree(str(CHROMA_DIR))
            shutil.copytree(str(chroma_src), str(CHROMA_DIR))
            print(f"  ✓ chroma")

        # uploads
        uploads_src = tmp_path / "data" / "uploads"
        if uploads_src.exists():
            if UPLOAD_DIR.exists():
                shutil.rmtree(str(UPLOAD_DIR))
            shutil.copytree(str(uploads_src), str(UPLOAD_DIR))
            print(f"  ✓ uploads")

        # images
        images_src = tmp_path / "data" / "images"
        if images_src.exists():
            if IMAGE_DIR.exists():
                shutil.rmtree(str(IMAGE_DIR))
            shutil.copytree(str(images_src), str(IMAGE_DIR))
            print(f"  ✓ images")

        # .env（可选）
        if restore_env:
            env_src = tmp_path / ".env"
            if env_src.exists():
                shutil.copy2(str(env_src), str(ENV_FILE))
                print(f"  ✓ .env")
            else:
                print(f"  - 备份中无 .env，跳过")

    # Step 6: 最终校验
    print(f"[恢复] 最终校验...")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        print(f"  ✓ 数据库可用: {tables} 张表, {files_count} 个文件, {chunks_count} 个 chunk")
    except Exception as e:
        print(f"  [!] 数据库校验失败: {e}")
        sys.exit(1)

    print(f"\n[恢复] ✅ 恢复完成！请启动伏羲服务验证。")
    print(f"  回滚点在 backups/pre-restore-*.zip（如需回滚，再次运行本脚本恢复）")


def main():
    parser = argparse.ArgumentParser(description="伏羲数据恢复")
    parser.add_argument("backup", type=str, help="备份 zip 文件路径")
    parser.add_argument("--force", action="store_true", help="跳过确认提示")
    parser.add_argument("--restore-env", action="store_true", help="同时恢复 .env 文件")
    args = parser.parse_args()

    do_restore(
        zip_path=Path(args.backup),
        force=args.force,
        restore_env=args.restore_env,
    )


if __name__ == "__main__":
    main()
