"""
lifecycle.py — 插件生命周期状态机

状态流转（严格单向，禁止半加载）：
  installed ──enable──▶ enabled ──disable──▶ disabled
     │                    │                     │
     └──── uninstall ─────┴─────────────────────┘
"""
import logging
from pathlib import Path

from src.plugins import registry

logger = logging.getLogger("rag.plugins.lifecycle")

VALID_STATUS = {"installed", "enabled", "disabled"}

# 合法流转表
_TRANSITIONS = {
    "installed": {"enabled", "uninstalled"},
    "enabled": {"disabled", "uninstalled"},
    "disabled": {"enabled", "uninstalled"},
}


def install(manifest: dict, plugin_dir: Path) -> dict:
    """安装插件：校验 manifest → 写入 registry"""
    # 1. 校验契约必备字段
    _validate_manifest(manifest)

    name = manifest["name"]
    # 2. 校验入口文件存在
    entry = manifest.get("entry", "main.py")
    entry_path = plugin_dir / entry
    if not entry_path.exists():
        raise ValueError(f"插件入口文件不存在: {entry_path}")

    # 3. 写入 registry（status=installed）
    registry.register(manifest)
    logger.info(f"插件已安装(installed): {name} v{manifest.get('version')}")
    return registry.get(name)


def enable(name: str) -> dict:
    _transition(name, "enabled")
    logger.info(f"插件已启用: {name}")
    return registry.get(name)


def disable(name: str) -> dict:
    _transition(name, "disabled")
    logger.info(f"插件已停用: {name}")
    return registry.get(name)


def uninstall(name: str, plugin_dir: Path = None) -> None:
    """卸载：从 registry 移除（文件清理由调用方决定）"""
    row = registry.get(name)
    if not row:
        raise ValueError(f"插件不存在: {name}")
    registry.remove(name)
    logger.info(f"插件已卸载: {name}")


def _transition(name: str, target: str) -> None:
    row = registry.get(name)
    if not row:
        raise ValueError(f"插件不存在: {name}")
    current = row["status"]
    if target not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"非法状态流转: {current} → {target}（插件 {name}）")
    registry.set_status(name, target)


def is_enabled(name: str) -> bool:
    row = registry.get(name)
    return bool(row and row["status"] == "enabled")


def _validate_manifest(manifest: dict) -> None:
    """校验 manifest 契约必备字段"""
    if not manifest.get("name"):
        raise ValueError("manifest 缺少 name")
    if not manifest.get("version"):
        raise ValueError(f"manifest 缺少 version ({manifest.get('name','?')})")
    if not manifest.get("api_version"):
        raise ValueError(f"manifest 缺少 api_version ({manifest.get('name','?')})")
    if manifest.get("kind") not in {"tool", "workflow"}:
        raise ValueError(f"manifest kind 非法: {manifest.get('kind')}")
