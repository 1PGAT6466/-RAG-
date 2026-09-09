"""
路由并发安全辅助模块
=================
伏羲的 FastAPI 路由统一是 async def，但大量路由内部调的是同步阻塞代码（SQLite、
Chroma、SeedDMS、embedding），这些同步代码直接在 async def 里跑会阻塞事件循环，
导致服务「冻住」——今天「删除崩溃」「导入卡死」的架构根因。

本模块提供两种安全模式（对齐 FastAPI 官方 + whisperX-FastAPI 成熟做法）：

模式 A：把路由从 `async def` 改成 `def`
  - FastAPI 自动把 def 路由丢进默认线程池执行（ThreadSafeExecutor），不阻塞事件循环
  - 最简单、最安全、零改动成本
  - 适用于：只调同步代码的路由（约 80% 的伏羲路由）

模式 B：async def 里需要调同步阻塞函数时，用 `await run_in_threadpool(fn, *args)`
  - Starlette/FastAPI 内置的 run_in_threadpool，把同步函数丢进线程池执行
  - 适用于：路由本身需要 async（如同时 await LLM + 同步 DB 查询）的混合场景

伏羲路由分类：
  - 纯同步（只调 storage/dms/graph）→ 模式 A（改 def）
  - 混合（async LLM + sync DB）→ 模式 B（run_in_threadpool 包裹同步部分）
  - 纯 async（httpx.AsyncClient LLM 调用）→ 不需改

用法：
  # 文件顶部
  from src.api._run import run_sync

  # 路由：原来是 async def，现在改 def
  def api_xxx(...):
      data = some_sync_function()   # 自动在线程池执行，不阻塞
      return ...

  # 混合场景（async def 里包裹同步调用）
  async def api_yyy(...):
      data = await run_sync(some_sync_function)
      result = await some_async_function()
      return ...
"""
from fastapi.concurrency import run_in_threadpool


async def run_sync(fn, *args, **kwargs):
    """把同步阻塞函数丢进线程池，返回 awaitable 结果。

    用于 async def 路由里需要调同步代码的场景，避免阻塞事件循环。
    """
    if kwargs:
        return await run_in_threadpool(fn, *args, **kwargs)
    return await run_in_threadpool(fn, *args)
