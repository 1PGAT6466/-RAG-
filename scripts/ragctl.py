"""ragctl - 伏羲 RAG 统一运维入口（Windows 优先，Linux 亦可用）

日常三件事：
  python scripts/ragctl.py status        # 一眼看服务/任务/磁盘状态
  python scripts/ragctl.py logs 100      # 看最近日志
  python scripts/ragctl.py backup        # 一致性备份（VACUUM 快照 + 校验）

疑难排查：
  python scripts/ragctl.py doctor        # 全链路体检，逐项 ✅/❌ + 修复建议
  python scripts/ragctl.py diag          # 一键打包诊断包（日志/快照/脱敏配置）成 zip
  python scripts/ragctl.py tasks         # 看入库任务（进行中/失败/死信/重试队列）

运维动作：
  python scripts/ragctl.py start|stop|restart
  python scripts/ragctl.py health        # HTTP 健康（失败自动降级诊断）
  python scripts/ragctl.py metrics       # 拉 /api/metrics 人类可读化
  python scripts/ragctl.py fts-reconcile # FTS 与 chunks 对账，清理孤儿
  python scripts/ragctl.py reset-vector  # 删除向量库（重启后重建）

说明：
  - 本脚本是「统一入口」，底层复用 backup.py / health_check.py 等既有能力，不重复实现。
  - 退出码：0=成功，1=失败/异常。可直接被 cron / 计划任务消费。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

# --- 路径与配置（单一事实源） ---
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
PID_FILE = DATA_DIR / "rag.pid"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "rag.db"
CHROMA_DIR = DATA_DIR / "chroma"
BACKUP_DIR = DATA_DIR / "backup"
DIAG_DIR = DATA_DIR / "diag"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8099"))
BASE_URL = f"http://{HOST}:{PORT}"

_TAG = "[ragctl]"


def _log(msg: str = ""):
    print(f"{_TAG} {msg}" if msg else "")


def _ok(msg: str):
    print(f"  [OK]   {msg}")


def _warn(msg: str):
    print(f"  [WARN] {msg}")


def _err(msg: str):
    print(f"  [FAIL] {msg}")


def _human_mb(n: float) -> str:
    if n >= 1024:
        return f"{n / 1024:.2f} GB"
    return f"{n:.1f} MB"


# ======================================================================
# 基础探测
# ======================================================================

def _pid_from_file() -> int:
    if not PID_FILE.exists():
        return 0
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return 0


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                               capture_output=True, text=True, timeout=5)
            return str(pid) in r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_on_port(port: int) -> int:
    """返回监听指定端口的 PID（0=无人监听）。"""
    try:
        if sys.platform == "win32":
            r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=8)
            for line in r.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line.upper():
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        return int(parts[-1])
        else:
            r = subprocess.run(["ss", "-lptn", f"sport = :{port}"],
                               capture_output=True, text=True, timeout=8)
            for line in r.stdout.splitlines():
                if "pid=" in line:
                    seg = line.split("pid=")[1].split(",")[0]
                    if seg.isdigit():
                        return int(seg)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return 0


def _is_running() -> tuple[bool, int]:
    """服务是否在跑：PID 文件 + 端口双重认定（更可靠）。"""
    pid = _pid_from_file()
    if pid and _process_alive(pid):
        return True, pid
    port_pid = _pid_on_port(PORT)
    if port_pid:
        return True, port_pid
    return False, 0


def _check_health(timeout: int = 5) -> dict:
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _db_query(sql: str, args: tuple = ()):
    """在只读连接里执行查询（不干扰运行中的服务）。"""
    import sqlite3
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _dir_size_mb(p: Path) -> float:
    if not p.exists():
        return 0.0
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1024 / 1024


# ======================================================================
# 启停
# ======================================================================

def cmd_start(_args) -> int:
    running, pid = _is_running()
    if running:
        _log(f"已在运行（PID: {pid}）")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "server.log"

    _log("正在启动服务...")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log_file, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=str(ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    for _ in range(30):
        time.sleep(1)
        if "error" not in _check_health():
            PID_FILE.write_text(str(proc.pid))
            _log(f"启动成功（PID: {proc.pid}）")
            _log(f"访问地址: {BASE_URL}")
            return 0

    _err(f"启动超时。请查看日志: {log_file}")
    return 1


def cmd_stop(_args) -> int:
    running, pid = _is_running()
    if not running:
        _log("服务未运行")
        PID_FILE.unlink(missing_ok=True)
        return 0

    _log(f"正在停止（PID: {pid}）...")
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, 15)
    except (subprocess.TimeoutExpired, OSError) as e:
        _warn(f"停止命令异常: {e}")
    PID_FILE.unlink(missing_ok=True)
    _log("已停止")
    return 0


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(2)
    return cmd_start(args)


# ======================================================================
# 状态（增强版）
# ======================================================================

def cmd_status(_args) -> int:
    running, pid = _is_running()
    _log("=" * 46)
    _log("伏羲 RAG 状态")
    _log("=" * 46)

    # 服务
    if running:
        _ok(f"服务: 运行中（PID: {pid}，端口 {PORT}）")
        h = _check_health(timeout=3)
        if "error" in h:
            _warn(f"HTTP 不可达（进程在但未响应）: {h['error']}")
        else:
            _ok(f"HTTP: {h.get('status', '?')}  依赖: {h.get('checks', {})}")
    else:
        _warn(f"服务: 未运行（端口 {PORT} 无监听）")

    # 数据
    if DB_PATH.exists():
        _ok(f"数据库: {_human_mb(DB_PATH.stat().st_size / 1024 / 1024)}")
    else:
        _err("数据库: 文件不存在")

    if CHROMA_DIR.exists():
        _ok(f"向量库: {_human_mb(_dir_size_mb(CHROMA_DIR))}")
    else:
        _warn("向量库: 目录不存在（可能使用 SQLite 兜底）")

    # 入库任务
    try:
        rows = _db_query("SELECT status, COUNT(*) FROM tasks GROUP BY status")
        if rows:
            dist = "，".join(f"{s}={n}" for s, n in rows)
            _ok(f"入库任务: {dist}")
        dl = _db_query("SELECT COUNT(*) FROM tasks WHERE dead_letter=1")[0][0]
        if dl:
            _warn(f"死信队列: {dl} 个任务需处理（ragctl tasks 查看）")
        pend = _db_query("SELECT COUNT(*) FROM tasks WHERE status='pending'")[0][0]
        if pend > 5:
            _warn(f"积压: {pend} 个 pending 任务")
    except Exception as e:
        _warn(f"任务查询失败: {e}")

    # 数据量
    try:
        files = _db_query("SELECT COUNT(*) FROM files")[0][0]
        chunks = _db_query("SELECT COUNT(*) FROM chunks")[0][0]
        _ok(f"文档: {files} 个 / 分块: {chunks} 个")
    except Exception:
        pass

    # 备份
    if BACKUP_DIR.exists():
        # 兼容两种命名：rag-*.zip（backup.py 实际产物）/ backup_*.zip
        zips = sorted(list(BACKUP_DIR.glob("rag-*.zip")) + list(BACKUP_DIR.glob("backup_*.zip")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if zips:
            ts = datetime.fromtimestamp(zips[0].stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            _ok(f"最近备份: {zips[0].name}（{ts}，共 {len(zips)} 份）")
        else:
            _warn("备份: 无（建议 ragctl backup）")

    # 磁盘
    try:
        import shutil as _sh
        usage = _sh.disk_usage(str(ROOT))
        free_gb = usage.free / 1024 / 1024 / 1024
        if free_gb < 5:
            _warn(f"磁盘剩余: {free_gb:.1f} GB（偏低）")
        else:
            _ok(f"磁盘剩余: {free_gb:.1f} GB")
    except Exception:
        pass

    _log("=" * 46)
    return 0


# ======================================================================
# 体检 doctor
# ======================================================================

def cmd_doctor(_args) -> int:
    _log("=" * 52)
    _log("伏羲 RAG 全链路体检")
    _log("=" * 52)
    problems = []

    # 1. 进程 / 端口
    running, pid = _is_running()
    if running:
        _ok(f"进程存活（PID: {pid}）")
    else:
        _warn("服务未运行（如需启动: ragctl start）")

    port_pid = _pid_on_port(PORT)
    if port_pid and running and pid != port_pid:
        _warn(f"端口 {PORT} 被 PID {port_pid} 占用，与 PID 文件({pid}) 不一致 → 删除 data/rag.pid 修正")
        problems.append("pid_mismatch")
    elif port_pid:
        _ok(f"端口 {PORT} 监听正常")

    # 2. HTTP 健康
    h = _check_health(timeout=5)
    if "error" in h:
        _warn(f"HTTP 不可达: {h['error']}")
        if running:
            problems.append("http_unreachable")
    else:
        st = h.get("status")
        checks = h.get("checks", {})
        if st == "ok":
            _ok(f"HTTP 健康: {checks}")
        else:
            _err(f"HTTP 降级: {checks}")
            problems.append("http_degraded")

    # 3. 数据库
    if DB_PATH.exists():
        try:
            _db_query("SELECT 1")
            _ok(f"数据库可读（{_human_mb(DB_PATH.stat().st_size / 1024 / 1024)}）")
        except Exception as e:
            _err(f"数据库读取失败: {e}")
            problems.append("db_unreadable")
    else:
        _err(f"数据库不存在: {DB_PATH}")
        problems.append("db_missing")

    # 3b. 数据一致性（复用 health_check.py）
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "health_check.py"),
                            "--db", str(DB_PATH)],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        if r.returncode == 0:
            _ok("数据一致性巡检通过（无孤儿 chunk / 计数一致 / FTS 行数一致）")
        else:
            _err("数据一致性巡检发现问题：")
            for line in (r.stdout or "").strip().splitlines()[-12:]:
                print(f"         {line}")
            problems.append("data_inconsistency")
    except Exception as e:
        _warn(f"一致性巡检无法执行: {e}")

    # 4. 向量库
    if CHROMA_DIR.exists():
        _ok(f"向量库目录存在（{_human_mb(_dir_size_mb(CHROMA_DIR))}）")
    else:
        _warn("向量库目录不存在（若 RAG_CHROMA=1 则异常）")

    # 5. FTS 对账
    try:
        conn_sql = ("SELECT (SELECT COUNT(*) FROM chunks) AS c, "
                    "(SELECT COUNT(*) FROM chunks_fts) AS f")
        row = _db_query(conn_sql)
        if row:
            c, f = row[0]
            if c == f:
                _ok(f"FTS 行数一致（chunks={c}, fts={f}）")
            else:
                _warn(f"FTS 不一致（chunks={c}, fts={f}）→ 运行 ragctl fts-reconcile")
                problems.append("fts_mismatch")
    except Exception:
        pass

    # 6. 配置 / 密钥
    env_file = ROOT / ".env"
    if env_file.exists():
        _ok(".env 存在")
        try:
            text = env_file.read_text(encoding="utf-8", errors="replace")
            if "JWT_SECRET=" in text:
                _ok("JWT_SECRET 已配置")
            else:
                _warn("JWT_SECRET 缺失（首次启动会自动生成）")
        except Exception:
            pass
    else:
        _warn(".env 不存在（将使用默认配置）")

    # 7. 磁盘
    try:
        usage = shutil.disk_usage(str(ROOT))
        free_gb = usage.free / 1024 / 1024 / 1024
        if free_gb < 5:
            _err(f"磁盘剩余仅 {free_gb:.1f} GB，需清理")
            problems.append("low_disk")
        else:
            _ok(f"磁盘剩余 {free_gb:.1f} GB")
    except Exception:
        pass

    # 8. 日志错误扫描
    log_file = LOG_DIR / "server.log"
    if log_file.exists():
        try:
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            errors = [l for l in tail if " ERROR " in l or " CRITICAL " in l]
            if errors:
                _warn(f"最近 500 行日志含 {len(errors)} 条 ERROR（ragctl logs 查看）")
            else:
                _ok("最近日志无 ERROR")
        except Exception:
            pass

    _log("-" * 52)
    if problems:
        _log(f"体检完成：发现 {len(problems)} 项需关注 → {', '.join(problems)}")
        _log("提示：ragctl diag 可打包诊断信息供排查")
        return 1
    _log("体检完成：全部正常 ✅")
    return 0


# ======================================================================
# 诊断包 diag
# ======================================================================

def _mask_secrets(text: str) -> str:
    """脱敏：API key / token / secret 只保留前 6 位。"""
    import re

    def _sub(m):
        sep = m.group(1)          # 原始分隔符（= 或 ==）
        val = m.group(2).strip().strip('"').strip("'")
        if not val:
            return m.group(0)
        return f"{m.group('key')}={val[:6]}***"

    # 支持 KEY=value 与 KEY==value（历史 .env 里有双等号脏值）
    pattern = r"(?P<key>[A-Z_]*?(?:API_KEY|TOKEN|SECRET|PASSWORD|PASS))\s*=(=?) *(\S+)"

    def _sub2(m):
        val = m.group(3).strip().strip('"').strip("'")
        if not val:
            return m.group(0)
        return f"{m.group('key')}={val[:6]}***"

    return re.sub(pattern, _sub2, text, flags=re.IGNORECASE)


def cmd_diag(_args) -> int:
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = DIAG_DIR / f"diag_{ts}.zip"

    _log(f"正在打包诊断信息 → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. 状态快照
        import io
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cmd_status(None)
            cmd_doctor(None)
        finally:
            sys.stdout = old
        zf.writestr("status.txt", buf.getvalue())

        # 2. 健康 & 指标（脱敏后）
        h = _check_health(timeout=5)
        zf.writestr("health.json", json.dumps(h, ensure_ascii=False, indent=2))

        # 3. 日志尾部
        log_file = LOG_DIR / "server.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
                zf.writestr("server.log.tail", "\n".join(lines))
            except Exception:
                pass
        for extra in ("service_err.log", "service_out.log"):
            p = LOG_DIR / extra
            if p.exists():
                try:
                    zf.writestr(extra, p.read_text(encoding="utf-8", errors="replace")[-50000:])
                except Exception:
                    pass

        # 4. 配置摘要（脱敏，只含非敏感键值统计）
        env_file = ROOT / ".env"
        if env_file.exists():
            try:
                masked = _mask_secrets(env_file.read_text(encoding="utf-8", errors="replace"))
                zf.writestr(".env.masked", masked)
            except Exception:
                pass

        # 5. DB 统计（不含内容）
        try:
            stats = {
                "files": _db_query("SELECT COUNT(*) FROM files")[0][0],
                "chunks": _db_query("SELECT COUNT(*) FROM chunks")[0][0],
                "entities": _db_query("SELECT COUNT(*) FROM entities")[0][0],
                "tasks_by_status": dict(_db_query("SELECT status, COUNT(*) FROM tasks GROUP BY status")),
            }
            zf.writestr("db_stats.json", json.dumps(stats, ensure_ascii=False, indent=2))
        except Exception:
            pass

        # 6. 环境信息
        zf.writestr("env.txt", "\n".join([
            f"platform={sys.platform}",
            f"python={sys.version}",
            f"root={ROOT}",
            f"host={HOST} port={PORT}",
            f"time={datetime.now().isoformat()}",
        ]))

    size_kb = zip_path.stat().st_size / 1024
    _ok(f"诊断包已生成: {zip_path}（{size_kb:.1f} KB，含脱敏）")
    _log("可直接发送给维护人员分析")
    return 0


# ======================================================================
# 任务 tasks
# ======================================================================

def cmd_tasks(args) -> int:
    show_all = getattr(args, "all", False)
    retry = getattr(args, "retry", False)

    _log("=" * 60)
    _log("入库任务")
    _log("=" * 60)

    if retry:
        # 把死信/失败任务重置为 pending 重跑
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            cur = conn.execute(
                "UPDATE tasks SET status='pending', retry_count=0, dead_letter=0, "
                "error=NULL, next_retry_at=0 WHERE dead_letter=1 OR status='failed'"
            )
            conn.commit()
            _ok(f"已重置 {cur.rowcount} 个失败/死信任务为 pending（服务会自动重跑）")
        finally:
            conn.close()
        return 0

    try:
        rows = _db_query(
            "SELECT task_id, filename, status, stage, progress, retry_count, "
            "dead_letter, error FROM tasks "
            "ORDER BY created_at DESC LIMIT 30"
        )
    except Exception as e:
        _err(f"查询失败: {e}")
        return 1

    if not rows:
        _log("暂无任务记录")
        return 0

    print(f"  {'task_id':<10} {'status':<9} {'stage':<12} {'进度':<5} {'重试':<4} 文件名")
    print("  " + "-" * 76)
    for tid, name, status, stage, prog, rc, dl, err in rows:
        flag = "!" if dl else " "
        name_s = (name or "")[:34]
        print(f"{flag} {tid:<10} {status:<9} {(stage or ''):<12} {prog or 0:<5} {rc or 0:<4} {name_s}")

    print()
    _log("处理失败任务: ragctl tasks --retry")
    _log("提示：入库任务状态在内存中易失，服务重启后由 tasks 表恢复")
    return 0


# ======================================================================
# 备份（复用 backup.py 的一致性快照 + 校验）
# ======================================================================

def cmd_backup(args) -> int:
    keep = getattr(args, "keep", 7)
    no_verify = getattr(args, "no_verify", False)

    try:
        from scripts.backup import do_backup  # type: ignore
    except Exception:
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from backup import do_backup  # type: ignore
        except Exception as e:
            _err(f"无法导入 backup.py: {e}")
            return 1

    try:
        _log("正在执行一致性备份（VACUUM 快照 + 校验）...")
        path = do_backup(dest_dir=BACKUP_DIR, keep=keep, verify=not no_verify)
        size_mb = path.stat().st_size / 1024 / 1024
        _ok(f"备份完成: {path.name}（{_human_mb(size_mb)}）")
        return 0
    except Exception as e:
        _err(f"备份失败: {e}")
        return 1


# ======================================================================
# 健康 / 指标 / 对账 / 日志 / 向量
# ======================================================================

def cmd_health(_args) -> int:
    h = _check_health(timeout=5)
    if "error" not in h:
        _log(f"状态: {h.get('status', '?')}")
        for k, v in (h.get("checks") or {}).items():
            _ok(f"{k}: {v}")
        return 0

    # 降级诊断：HTTP 不通时，逐层排查
    _err(f"HTTP 不可达: {h['error']}")
    _log("降级诊断:")
    running, pid = _is_running()
    if running:
        _warn(f"进程存在（PID: {pid}）但 HTTP 无响应 → 可能仍在启动中，或卡在初始化")
        _log("  建议: ragctl logs 100 查看启动日志")
    else:
        _warn(f"进程未运行，端口 {PORT} 无监听")
        _log("  建议: ragctl start")
    port_pid = _pid_on_port(PORT)
    if port_pid and port_pid != pid:
        _warn(f"端口 {PORT} 被 PID {port_pid} 占用")
    return 1


def cmd_metrics(args) -> int:
    """拉取 /api/metrics（需管理员 token）。"""
    token = getattr(args, "token", None) or os.getenv("RAG_ADMIN_TOKEN")
    if not token:
        _warn("/api/metrics 需要管理员 token")
        _log("用法: ragctl metrics --token <JWT>   或设置环境变量 RAG_ADMIN_TOKEN")
        _log("提示: 内网轻量场景可直接看 ragctl status 的统计")
        return 1
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/metrics",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            m = json.loads(resp.read())
    except Exception as e:
        _err(f"获取指标失败: {e}")
        return 1

    _log("=" * 46)
    _log("运行指标（最近 60 秒窗口）")
    _log("=" * 46)
    http = m.get("http", {})
    _ok(f"HTTP: {http.get('requests_total', 0)} 请求，错误率 {http.get('error_rate', 0):.1%}")
    lat = http.get("latency", {})
    _ok(f"延迟: 平均 {lat.get('avg_ms', 0):.0f}ms  p95 {lat.get('p95_ms', 0):.0f}ms  p99 {lat.get('p99_ms', 0):.0f}ms")
    llm = m.get("llm", {})
    _ok(f"LLM: {llm.get('calls', 0)} 次调用，失败率 {llm.get('failure_rate', 0):.1%}，降级 {llm.get('degrades', 0)} 次")
    tasks = m.get("tasks", {})
    _ok(f"任务: 成功 {tasks.get('success', 0)} / 失败 {tasks.get('failure', 0)} / 重试 {tasks.get('retry', 0)}")
    cache = m.get("cache", {})
    _ok(f"缓存: 命中率 {cache.get('hit_rate', 0):.1%}（命中 {cache.get('hits', 0)} / 未中 {cache.get('misses', 0)}）")
    return 0


def cmd_fts_reconcile(_args) -> int:
    """FTS 与 chunks 对账，清理孤儿。"""
    try:
        from src.storage.chunks import reconcile_fts
        result = reconcile_fts()
        _ok(f"FTS 对账完成: {result}")
        return 0
    except Exception as e:
        _err(f"对账失败: {e}")
        return 1


def cmd_logs(args) -> int:
    n = getattr(args, "n", 50)
    log_file = LOG_DIR / "server.log"
    if not log_file.exists():
        _warn("无日志文件")
        return 1
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        for line in lines[-n:]:
            print(line)
    except Exception as e:
        _err(f"读取日志失败: {e}")
        return 1
    return 0


def cmd_reset_vector(_args) -> int:
    if CHROMA_DIR.exists():
        _log(f"删除向量库: {CHROMA_DIR}")
        shutil.rmtree(CHROMA_DIR)
        _ok("向量库已删除。重启服务后将自动从 SQLite chunks 重建。")
    else:
        _warn("向量库目录不存在")
    return 0


# ======================================================================
# 入口
# ======================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ragctl", description="伏羲 RAG 统一运维入口",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("start", help="启动服务")
    sub.add_parser("stop", help="停止服务")
    sub.add_parser("restart", help="重启服务")
    sub.add_parser("status", help="看状态（服务/任务/数据/备份/磁盘）")
    sub.add_parser("doctor", help="全链路体检 + 修复建议")
    sub.add_parser("diag", help="打包诊断信息成 zip（脱敏）")
    sub.add_parser("health", help="HTTP 健康检查（失败自动降级诊断）")
    sub.add_parser("fts-reconcile", help="FTS 与 chunks 对账，清理孤儿")
    sub.add_parser("reset-vector", help="删除向量库（重启后重建）")

    t = sub.add_parser("tasks", help="查看入库任务")
    t.add_argument("--all", action="store_true", help="显示全部")
    t.add_argument("--retry", action="store_true", help="重置失败/死信任务为 pending")

    b = sub.add_parser("backup", help="一致性备份（VACUUM 快照 + 校验）")
    b.add_argument("--keep", type=int, default=7, help="保留最近 N 份")
    b.add_argument("--no-verify", action="store_true", help="跳过校验")

    lg = sub.add_parser("logs", help="查看日志尾")
    lg.add_argument("n", nargs="?", type=int, default=50, help="行数，默认 50")

    mt = sub.add_parser("metrics", help="拉取 /api/metrics（需 token）")
    mt.add_argument("--token", type=str, default=None, help="管理员 JWT")

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    handlers = {
        "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
        "status": cmd_status, "doctor": cmd_doctor, "diag": cmd_diag,
        "health": cmd_health, "tasks": cmd_tasks, "backup": cmd_backup,
        "logs": cmd_logs, "metrics": cmd_metrics, "reset-vector": cmd_reset_vector,
        "fts-reconcile": cmd_fts_reconcile,
    }
    try:
        return handlers[args.cmd](args)
    except KeyboardInterrupt:
        _log("已中断")
        return 130
    except Exception as e:
        _err(f"命令执行异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
