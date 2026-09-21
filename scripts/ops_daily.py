"""伏羲 RAG 每日巡检（定时任务用）

用途：被 cron / 计划任务调用，执行 backup + doctor，把结果写入
      data/logs/ops_daily.log，并在发现问题时以非零退出码结束（供告警链路消费）。

用法：
    python scripts/ops_daily.py            # 备份 + 体检
    python scripts/ops_daily.py --no-backup  # 只体检

设计原则：
    - 零外部依赖，纯 stdlib + 调用 ragctl / backup.py
    - 不修改任何业务数据（体检只读；备份是独立快照）
    - 幂等：可重复执行
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "data" / "logs" / "ops_daily.log"


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

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"\n{'=' * 60}", f"[{ts}] 伏羲每日巡检开始", f"{'=' * 60}"]
    problems = []

    # 1. 备份
    if not args.no_backup:
        code, out = _run([py, str(ROOT / "scripts" / "ragctl.py"), "backup", "--keep", str(args.keep)])
        lines.append(f"\n--- 备份 (退出码 {code}) ---")
        lines.append(out.strip())
        if code != 0:
            problems.append("backup_failed")

    # 2. 体检
    code, out = _run([py, str(ROOT / "scripts" / "ragctl.py"), "doctor"])
    lines.append(f"\n--- 体检 (退出码 {code}) ---")
    lines.append(out.strip())
    if code != 0:
        problems.append("doctor_issues")

    # 3. 汇总
    lines.append("")
    if problems:
        lines.append(f"[{ts}] 巡检发现 {len(problems)} 项问题：{', '.join(problems)}")
        lines.append("处置建议：python scripts/ragctl.py diag  然后查看诊断包")
    else:
        lines.append(f"[{ts}] 巡检通过 ✅ 全部正常")

    report = "\n".join(lines)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(report + "\n")

    # 输出到控制台（供 cron 采集）
    print(report)

    # 仅保留最近约 5000 行，防无限增长
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(content) > 5000:
            LOG_FILE.write_text("\n".join(content[-5000:]) + "\n", encoding="utf-8")
    except Exception:
        pass

    # 有异常给短摘要（便于告警正文）
    if problems:
        print(f"\n[ALERT] 伏羲巡检异常: {', '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
