"""
hooks.py — Workflow hook 调度器（插件挂载主链路节点）

设计原则（对齐设计文档 §5）：
  1. 非阻塞 —— hook 在后台/容错执行，绝不拖慢主链路
  2. 可降级 —— 任何插件抛错、超时、崩溃，只记日志，不影响主流程结果
  3. 声明式 —— 插件通过 manifest.hooks 声明挂载点，无需改主链路硬编码

支持的事件（event name）：
  on_ingest   入库完成（chunks 已写入，返回前触发）
  on_search   检索完成（rerank 之后、返回前触发）
"""
import logging
import traceback

from src.plugins import registry, host

logger = logging.getLogger("rag.plugins.hooks")


def _get_hook_plugins(event: str) -> list[tuple[str, dict]]:
    """返回声明了该 event 的已启用插件 [(name, hook_def), ...]"""
    result = []
    for row in registry.list_all():
        if row["status"] != "enabled":
            continue
        manifest = registry.get_manifest(row["name"])
        if not manifest:
            continue
        hooks = manifest.get("hooks", {})
        if isinstance(hooks, dict) and event in hooks:
            result.append((row["name"], hooks[event]))
    return result


def run_hook(event: str, payload: dict, timeout: float = 5.0) -> dict:
    """
    同步执行某个事件的所有 hook（串行，带超时 + 容错）。

    返回 {
      "ok": bool,            # 是否有插件成功处理
      "calls": [ {...} ],    # 每个 hook 的调用结果摘要
      "payload": dict,       # 经过 hook 可能修改后的 payload
    }

    payload 会被 hook 返回的 result 合并/覆盖（可选，hook 可返回 None 表示不改）。
    单个 hook 失败不影响其他 hook 和主链路。
    """
    plugins = _get_hook_plugins(event)
    if not plugins:
        return {"ok": False, "calls": [], "payload": payload}

    calls = []
    any_ok = False
    pool = host.get_pool()

    import src.plugins as _plugins_mod

    for pname, hook_def in plugins:
        method = hook_def.get("method", event)
        manifest = registry.get_manifest(pname) or {}
        entry = manifest.get("entry", "main.py")
        entry_path = str(_plugins_mod.PLUGINS_DIR / pname / entry)

        call_entry = {"plugin": pname, "method": method}
        try:
            # 传给 hook 的上下文：事件名 + payload
            res = pool.invoke(pname, entry_path, method,
                              {"event": event, "payload": payload},
                              timeout=timeout)
            if "result" in res:
                any_ok = True
                call_entry["status"] = "ok"
                # 允许 hook 修改 payload
                if isinstance(res["result"], dict):
                    payload = {**payload, **res["result"]}
            else:
                call_entry["status"] = "error"
                call_entry["error"] = res.get("error", "unknown")
                logger.warning(f"[hook] {event} 插件 {pname} 返回错误: {res.get('error')}")
        except Exception as e:
            call_entry["status"] = "exception"
            call_entry["error"] = str(e)
            logger.warning(f"[hook] {event} 插件 {pname} 异常: {e}\n{traceback.format_exc()}")

        calls.append(call_entry)

    return {"ok": any_ok, "calls": calls, "payload": payload}


async def run_hook_async(event: str, payload: dict, timeout: float = 5.0) -> dict:
    """异步包装：在 executor 中跑，避免阻塞事件循环"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_hook, event, payload, timeout)
