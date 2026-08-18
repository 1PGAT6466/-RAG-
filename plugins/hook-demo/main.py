"""Hook 演示插件 — 挂载 on_search / on_ingest 节点"""


def on_search(event, payload):
    """检索后处理：给每条结果追加 hook 来源标记。

    返回 dict 会与 payload 合并；这里返回新的 results（带标记）来演示「钩子改写结果」。
    """
    results = payload.get("results", [])
    query = payload.get("query", "")
    marked = []
    for r in results:
        r2 = dict(r)
        r2["_hook_source"] = "hook-demo"
        marked.append(r2)
    return {"results": marked, "_hook_note": f"hook 处理了 {len(marked)} 条结果，query={query[:20]}"}


def on_ingest(event, payload):
    """入库后处理：返回一个标记字段，证明 hook 执行过。

    注意：插件 stdout 用于 JSON-RPC 通信，不能 print。
    """
    return {"ingest_hooked": True, "file_id": payload.get("file_id")}
