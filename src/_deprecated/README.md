# _deprecated — 已废弃代码

此目录存放因重构而不再使用的旧代码，仅作历史参考，**不参与运行时导入**。

| 文件 | 废弃原因 | 替代 |
|------|---------|------|
| `task_queue.py` | 早期同步上传队列实现，已被 `src/pipeline/engine.py`（IngestEngine）完全取代。旧的 `on_ingest` hook 已迁移到 `engine._run_on_ingest_hook` | `src/pipeline/engine.py` |

> 注意：此目录下代码不会被任何模块 import，删除不影响运行。保留仅用于考古或迁移参考时回溯。
