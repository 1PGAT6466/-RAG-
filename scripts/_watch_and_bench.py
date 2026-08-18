"""
scripts/_watch_and_bench.py — 等当前 OCR 重建(file_id=4)完成后自动跑速度基准（一次性脚本）

监视 data/rag.db 中 file_id=4 的 chunks 数量：
  - chunks > 0 → 重建成功，等 90s 收尾后跑 bench_ocr.py，结果写 data/bench_ocr_result.txt
  - 进程 23096 退出且 chunks=0 → 重建失败，记录退出
"""
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

DB = "data/rag.db"
LOG = ROOT / "data" / "watch_bench.log"
FID = 4
WATCH_PID = 23096


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def chunks_count(fid):
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        n = c.execute("select count(*) from chunks where file_id=?", (fid,)).fetchone()[0]
        c.close()
        return n
    except Exception:
        return -1


def proc_alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                       capture_output=True, text=True)
    return str(pid) in r.stdout


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    row = c.execute("select path from files where id=?", (FID,)).fetchone()
    c.close()
    if not row:
        log(f"file_id={FID} 不存在，退出")
        return
    pdf = row[0]

    deadline = time.time() + 50 * 60
    while time.time() < deadline:
        n = chunks_count(FID)
        if n > 0:
            log(f"重建完成 chunks={n}")
            break
        if not proc_alive(WATCH_PID):
            time.sleep(10)
            n = chunks_count(FID)
            if n > 0:
                log(f"进程已退出，chunks={n}，重建成功")
                break
            log(f"进程 {WATCH_PID} 已退出且 chunks=0 —— 重建可能失败")
            return
        time.sleep(20)
    else:
        log("等待超时（50 分钟），放弃基准")
        return

    time.sleep(90)  # 等 Chroma 写入 + 实体抽取收尾，避免 GPU/CPU 争用
    log("开始 OCR 速度基准（g1/g2/g1c960/g2c960，约 4-5 分钟）…")
    r = subprocess.run(
        [sys.executable, "scripts/bench_ocr.py", "--path", pdf,
         "--first", "200", "--last", "229"],
        capture_output=True, text=True, cwd=str(ROOT))
    out = ROOT / "data" / "bench_ocr_result.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(r.stdout + "\n--- STDERR ---\n" + (r.stderr or "")[-3000:])
    log(f"基准完成 → {out}")
    print(r.stdout)


if __name__ == "__main__":
    main()
