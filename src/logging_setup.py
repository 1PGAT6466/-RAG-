"""
结构化日志配置
============
在保持原有控制台日志可读性的基础上，追加 JSON 结构化文件输出 + 按日轮转。

设计：
  - 控制台 handler：人类可读的文本格式（不变，保持现有排查体验）
  - 文件 handler：JSON Lines 格式（每行一条 JSON），字段含时间/级别/模块/消息/请求ID/耗时，
    便于用 jq / grep / ELK 采集分析
  - TimedRotatingFileHandler 按天轮转，保留 14 天，旧日志自动清理，避免磁盘膨胀

用法：
  from src.logging_setup import setup_logging
  setup_logging()   # server.py 启动时调用一次

可通过 .env 关闭文件输出（RAG_JSON_LOG=0），生产环境默认开启。
"""
import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

# 默认日志目录（相对项目根）
_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = _BASE_DIR / "logs"


class JsonFormatter(logging.Formatter):
    """JSON Lines 结构化格式器，含请求上下文（trace_id / 耗时）"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 注入请求上下文（request_logger 中间件写入的额外属性）
        for k in ("trace_id", "method", "path", "status_code", "elapsed_ms"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        # 异常堆栈
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: str | Path | None = None) -> logging.Logger:
    """配置根 logger：控制台文本 + 文件 JSON（按日轮转）。

    幂等：重复调用不会重复添加 handler（用标志位保护）。
    """
    root = logging.getLogger()
    # 避免重复初始化（如热重载/重复 import）
    if getattr(setup_logging, "_done", False):
        return logging.getLogger("rag")
    setup_logging._done = True

    # 是否启用 JSON 文件日志（默认开，RAG_JSON_LOG=0 关闭）
    json_log_enabled = os.environ.get("RAG_JSON_LOG", "1") == "1"

    # 1) 控制台 handler（保持原有可读性）
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # 2) 文件 JSON handler（按日轮转，保留 14 天）
    if json_log_enabled:
        try:
            _dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
            _dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.TimedRotatingFileHandler(
                filename=str(_dir / "rag.log"),
                when="midnight",
                interval=1,
                backupCount=14,
                encoding="utf-8",
            )
            fh.suffix = "%Y-%m-%d"  # 轮转后文件名 rag.log.2026-08-27
            fh.setFormatter(JsonFormatter())
            root.addHandler(fh)
        except Exception as e:
            # 文件日志失败不阻断服务，仅打一条控制台警告
            logging.getLogger("rag").warning(f"JSON 日志文件初始化失败（继续控制台日志）: {e}")

    root.setLevel(logging.INFO)
    return logging.getLogger("rag")
