"""伏羲 RAG 每日巡检（定时任务用）

用途：被 cron / 计划任务调用，执行 backup + doctor，把结果写入
      <归档目录>/<YYYYMD>/ops_daily.md（按天建文件夹，当天覆盖）。
      发现问题时以非零退出码结束（供告警链路消费）。

归档目录（可用环境变量 RAG_OPS_ARCHIVE 覆盖）：
    E:\测试项目\自建知识库\伏羲运维手册\日志\<YYYYMD>\
        例：2026-09-21 → 2026921

用法：
    python scripts/ops_daily.py            # 备份 + 体检
    python scripts/ops_daily.py --no-backup  # 只体检

设计原则：
    - 零外部依赖，纯 stdlib + 调用 ragctl / backup.py
    - 不修改任何业务数据（体检只读；备份是独立快照）
    - 幂等：可重复执行，当天多次运行覆盖旧报告
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 归档目录：与 ragctl 保持一致（按天建文件夹，当天覆盖）
# \u53ef\u7528\u73af\u5883\u53d8\u91cf RAG_OPS_ARCHIVE \u8986\u76d6
ARCHIVE_ROOT = Path(os.getenv("RAG_OPS_ARCHIVE", r"E:\测试项目\自建知识库\伏羲运维手册\日志"))


def _today_dir() -> Path:
    """当天归档目录：<ARCHIVE_ROOT>/<YYYYMD>（如 2026921）。归档盘不可用时回退本地。"""
    now = datetime.now()
    name = f"{now.year}{now.month}{now.day}"
    d = ARCHIVE_ROOT / name
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = ROOT / "data" / "ops_archive" / name
        d.mkdir(parents=True, exist_ok=True)
    return d


def _run(args: list) -> tuple[int, str]:
    """执行子命令，返回 (退出码, 合并输出)。"""
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=600, cwd=str(ROOT), encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, "[超时] 命令执行超过 600 秒"
    except Exception as e:
        return 1, f"[异常] {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="伏羲 RAG 每日巡检")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份，只体检")
    parser.add_argument("--keep", type=int, default=7, help="备份保留份数")
    args = parser.parse_args()

    LOG_FILE = _today_dir() / "ops_daily.md"
    py = sys.executable
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    problems = []

    # 1. 备份
    backup_code, backup_out = (0, "（已跳过）")
    if not args.no_backup:
        backup_code, backup_out = _run(
            [py, str(ROOT / "scripts" / "ragctl.py"), "backup", "--keep", str(args.keep)])
        if backup_code != 0:
            problems.append("backup_failed")

    # 2. 体检
    doctor_code, doctor_out = _run([py, str(ROOT / "scripts" / "ragctl.py"), "doctor"])
    if doctor_code != 0:
        problems.append("doctor_issues")

    # 3. 组装 Markdown 报告
    status_badge = "🔴 异常" if problems else "🟢 正常"
    backup_cell = "⏭️ 已跳过" if args.no_backup else ("✅ 成功" if backup_code == 0 else "❌ 失败")
    md = [f"# 伏羲 RAG 每日巡检报告（{datetime.now().strftime('%Y-%m-%d')}）", "",
          f"> 执行时间：{ts}", ">", f"> 总体状态：{status_badge}", "",
          "## 巡检摘要", "", "| 项目 | 结果 |", "| --- | --- |",
          f"| 数据备份 | {backup_cell} |",
          f"| 全链路体检 | {'✅ 通过' if doctor_code == 0 else '❌ 需关注'} |", ""]

    if problems:
        md += ["## ❌ 需要处理", ""]
        if "backup_failed" in problems:
            md.append("- **备份失败**：请检查磁盘空间与 data/backup 目录")
        if "doctor_issues" in problems:
            md.append("- **体检异常**：详见下方体检输出，或运行 `python scripts/ragctl.py diag` 生成诊断包")
        md.append("")

    md += ["## 📦 备份输出", "", "```", backup_out.strip(), "```", "",
           "## 🩺 体检输出", "", "```", doctor_out.strip(), "```", ""]

    report = "\n".join(md)
    # 写入当天归档目录（当天多次巡检覆盖旧版，不追加）
    with open(LOG_FILE, "w", encoding="utf-8-sig") as f:
        f.write(report + "\n")

    # 输出简洁摘要到控制台（供 cron 采集；完整报告已存 Markdown）
    print(f"[{ts}] 伏羲每日巡检：{status_badge}")
    print(f"  报告: {LOG_FILE}")

    # 有异常给短摘要（便于告警正文）
    if problems:
        print(f"\n[ALERT] 伏羲巡检异常: {', '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
