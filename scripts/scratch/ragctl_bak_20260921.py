"""ragctl - RAG system control utility

Usage:
  python scripts/ragctl.py start
  python scripts/ragctl.py stop
  python scripts/ragctl.py restart
  python scripts/ragctl.py status
  python scripts/ragctl.py backup
  python scripts/ragctl.py health
  python scripts/ragctl.py logs [n]
  python scripts/ragctl.py reset-vector

Runs on Windows. For Linux/Docker, use server.py directly.
"""
import subprocess
import sys
import os
import time
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
PID_FILE = ROOT / "data" / "rag.pid"
LOG_DIR = ROOT / "data" / "logs"
DB_PATH = ROOT / "data" / "rag.db"
BACKUP_DIR = ROOT / "data" / "backup"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8099"))
BASE_URL = f"http://{HOST}:{PORT}"


def _is_running() -> tuple[bool, int]:
    """Check if server is running. Returns (running, pid)."""
    if not PID_FILE.exists():
        return False, 0
    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process exists
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5
        )
        if str(pid) in result.stdout and "python" in result.stdout.lower():
            return True, pid
    except (ValueError, subprocess.TimeoutExpired):
        pass
    return False, 0


def _check_health() -> dict:
    """Check HTTP health endpoint."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def cmd_start():
    running, pid = _is_running()
    if running:
        print(f"[ragctl] Already running (PID: {pid})")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "server.log"

    print("[ragctl] Starting server...")
    # Start server in background
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=str(ROOT),
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    # Wait for startup
    for i in range(15):
        time.sleep(1)
        health = _check_health()
        if "error" not in health:
            PID_FILE.write_text(str(proc.pid))
            print(f"[ragctl] Started successfully (PID: {proc.pid})")
            print(f"[ragctl] URL: {BASE_URL}")
            return 0

    print(f"[ragctl] Startup timeout. Check log: {log_file}")
    return 1


def cmd_stop():
    running, pid = _is_running()
    if not running:
        print("[ragctl] Not running")
        if PID_FILE.exists():
            PID_FILE.unlink()
        return 0

    print(f"[ragctl] Stopping (PID: {pid})...")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        pass
    PID_FILE.unlink(missing_ok=True)
    print("[ragctl] Stopped")
    return 0


def cmd_restart():
    cmd_stop()
    time.sleep(2)
    return cmd_start()


def cmd_status():
    running, pid = _is_running()
    print(f"[ragctl] Server: {'running' if running else 'stopped'}", end="")
    if running:
        print(f" (PID: {pid})")
    else:
        print()

    # Database
    if DB_PATH.exists():
        size_mb = DB_PATH.stat().st_size / 1024 / 1024
        print(f"[ragctl] Database: {size_mb:.1f} MB")
    else:
        print("[ragctl] Database: not found")

    # Vector store
    chroma_dir = ROOT / "data" / "chroma"
    if chroma_dir.exists():
        print("[ragctl] Vector store: exists")
    else:
        print("[ragctl] Vector store: not found")

    # Backups
    if BACKUP_DIR.exists():
        backups = sorted(BACKUP_DIR.glob("rag_*.db"), reverse=True)
        if backups:
            print(f"[ragctl] Latest backup: {backups[0].name}")
        else:
            print("[ragctl] Backups: none")
    return 0


def cmd_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        print("[ragctl] Database not found, nothing to backup")
        return 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"rag_{ts}.db"
    shutil.copy2(DB_PATH, backup_file)
    size_mb = backup_file.stat().st_size / 1024 / 1024
    print(f"[ragctl] Backup created: {backup_file.name} ({size_mb:.1f} MB)")
    return 0


def cmd_health():
    health = _check_health()
    if "error" in health:
        print(f"[ragctl] Unreachable: {health['error']}")
        return 1

    print(f"[ragctl] Status: {health.get('status', 'unknown')}")
    print(f"[ragctl] Files: {health.get('files', '?')}")
    print(f"[ragctl] Chunks: {health.get('chunks', '?')}")
    print(f"[ragctl] Vector DB: {health.get('vector_db', '?')}")
    return 0


def cmd_logs(n=50):
    log_file = LOG_DIR / "server.log"
    if not log_file.exists():
        print("[ragctl] No log file found")
        return 1
    lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
    for line in lines[-n:]:
        print(line)
    return 0


def cmd_reset_vector():
    """Delete and rebuild vector store from SQLite chunks."""
    chroma_dir = ROOT / "data" / "chroma"
    if chroma_dir.exists():
        print(f"[ragctl] Deleting vector store: {chroma_dir}")
        shutil.rmtree(chroma_dir)
    print("[ragctl] Vector store deleted. Restart server to trigger rebuild.")
    return 0


def cmd_help():
    print(__doc__)
    return 0


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "backup": cmd_backup,
    "health": cmd_health,
    "logs": cmd_logs,
    "reset-vector": cmd_reset_vector,
    "help": cmd_help,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        cmd_help()
        return 1

    cmd = sys.argv[1]
    if cmd == "logs" and len(sys.argv) > 2:
        return cmd_logs(int(sys.argv[2]))
    return COMMANDS[cmd]()


if __name__ == "__main__":
    sys.exit(main())
