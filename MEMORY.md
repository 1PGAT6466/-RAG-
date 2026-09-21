# MEMORY.md — 长期记忆索引

> 2026-09-21 起改为「精简索引」：历史全文已归档到 `docs/archive/MEMORY-2026-09-21.md`（205KB）。
> 本文件只保留跨会话必须知道的**持久事实与偏好**。详细历史请查归档。

## 🔴 项目基线（长期有效）

- **名称**：对外「伏羲 RAG」，工作目录/仓库「更新RAG框架」（同一系统）。
- **技术栈**：FastAPI + Vue3 + SQLite(WAL) + ChromaDB + 本地 BGE(bge-large-zh-v1.5) + 自建混合检索 + SeedDMS 对接。
- **规模**：后端 src ~13.7k 行，前端 ~7.8k 行。
- **唯一权威工程约定**：`AGENTS.md`。结构日志：`docs/structure/SYSTEM.md`。问题跟踪：`问题整改清单.md`。
- **验证方式**：`pytest`（组件回归）+ `scripts/smoke_test.py`（端到端）。改检索跑 `scripts/retrieval_benchmark.py`。

## 🔴 用户长期偏好与工作原则

1. 保持系统「纯净、统一、有序」，不盲目大改、不堆砌。
2. 优化目标恒定为：稳定性、性能、向量化速率、多格式转化成功率、回复标准度。
3. **模块式借鉴**：某开源项目某条链路完成度明显更高时，整体替换那条链路，而非缝缝补补。
4. **实测优先**：先深入代码 + 多轮实测穷尽缺陷，再动手；不只听已知 bug。
5. 交付模式：先出「深度诊断报告」→ 用户确认 → 逐项改。
6. 用户会要求「把结论补充进文档，并按要求修复」——需边改边在文档标记进度。
7. 对标开源：RAGFlow / LightRAG / MinerU / docling / unstructured / LlamaIndex（只学构造，不引重依赖）。

## 🔴 关键工程事实（跨会话必知）

- **时间戳全 UTC**（2026-09-21 迁移 005 起）：新增 SQL 禁用 `datetime('now','localtime')`，用 `datetime('now')`。
- **迁移通道收敛**：`init_db()` 启动自动跑 `migrations/` pending 并维护 `schema_version`。schema 变更一律写编号迁移文件。
- **rerank_cache 唯一权威定义在 `connection.py` SCHEMA**：`(q, top_k, fp, results_json, created_at)` 复合主键；`rerank.py` 不得再重定义（历史上重复定义导致持久缓存静默失效）。
- **重排三级统一 0-1 刻度**：SiliconFlow 原生 0-1，DeepSeek 归一化(÷10)，本地 TF-IDF min-max 融合 RRF。新打分器必须同刻度。
- **FTS 无 trigger**：插入走 `segment_for_fts`(jieba)；删除必须同时删 FTS 双表 + `chunks` 本体；启动 `reconcile_fts()` 对账孤儿。
- **`mcp` 必须 pin `>=1.0.0,<2.0.0`**：2.x 会装 httpx2 顶掉 httpx，全项目 HTTP 崩溃。
- **SPA fallback 吞路由**：新增 API 必须挂 `/api/` 前缀，否则返回 200+HTML。
- **pydantic/chromadb/PyMuPDF/httpx 已加上界**（缺陷D 修复）。
- **上传白名单**：`config.UPLOAD_ALLOWED_EXT`（pdf/doc/docx/xls/xlsx/ppt/pptx/txt/csv/md/log/json/xml）；上传走临时文件流式 + 增量 sha256。
- **feedback 写接口限流** 30/min per-user + `(user_id,kind,query,chunk_ids)` 幂等去重。
- **破坏性脚本**（wipe_data/reset_data）必须 `--yes`，且自动备份 rag.db。
- **示例脚本**：`scripts/scratch/` 放临时调试脚本；正式脚本在 `scripts/` 根。
- **守护进程**：#17 产物 = `scripts/install_service.cmd`（Windows/NSSM）+ `scripts/fuxi-rag.service`（Linux systemd），保持 workers=1（SQLite 单写者），崩溃自动重拉；属部署产物，开发机未安装。
- **测试基础设施**：`tests/test_e2e_flow.py`=离线打桩 e2e（永远可跑）；`tests/test_e2e_real_flow.py`=真实闭环 e2e（真实 BGE+存储+检索，有模型则跑无则 skip，`RAG_E2E_REAL=force/0` 强制）。真实 e2e 用全局串行锁；测试改 DB 路径必须改 `connection.DB_PATH`（该模块顶层 `from config import DB_PATH` 快照，只改 config 无效）。
- **异步测试一律 `asyncio.run()`**：禁用 `asyncio.get_event_loop()`（Python 3.11 下若同进程先跑过 TestClient 会抛 no current event loop）。

## 🔴 SeedDMS（原件唯一存储）

- 容器 `seeddms`，固化镜像 **`seeddms-fuxi:latest`**（含 LibreOffice + unoconv shim + TablePreview + 定制 ViewDocument）。重建必须用此镜像。
- **生效配置** = 宿主机 `E:\测试项目\SeedDMS\conf\settings.xml`（→ 容器 `/var/lib/seeddms/conf/settings.xml`）；容器内 `/home/www-data/seeddms60x/conf/` 那份**不生效**。
- 代码目录不在 bind mount，改完**必须 `docker commit seeddms seeddms-fuxi:latest`**。
- 账号 admin/admin；DB 是 SQLite（`content.db`）；直写库契约见 `src/dms/writer.py` 头注释。
- API 只读，无创建文档能力 → 上传走「直接写库」路线。
- 表格预览：`op.TablePreview.php`（PhpSpreadsheet + 服务端分页 1000 行/页）。
- 运维手册已交付（2026-09-21，含备份/重建/红线）。

## 近期已落地（2026-09-21，《问题整改清单》核查 + 批量修复）

- 缺陷A rerank_cache 结构冲突（生产 bug，持久缓存从未生效）— 已修 + 库自愈重建。
- #1 迁移收敛 / #2 时间戳统一 / #3 连接统一 / #4 直连整改 / #7 连接关闭 / #8 rerank TTL / #9 FTS 对账 / #10 缓存清理 / #11 重排刻度 / #12 历史滑窗 / #13+#缺陷B 日志级别 / #14 key 归一化 / #16 破坏性脚本保护 / #17 守护进程 / #21 上传白名单 / #22 feedback 限流 / #23 上传流式化 / #24 task_id 契约 / #25 备份整理 / 缺陷C 超限内存 / 缺陷D 依赖上界 / 缺陷E 残留文件 / 缺陷F delete_file 漏删 chunks / #5 AGENTS 重写 / #6 名称统一 / #18 scripts 整理 / #19 docs 索引 / #20 MEMORY 归档。
- **#15 真实闭环 e2e 已补齐**（2026-09-21 追加）：`tests/test_e2e_real_flow.py` 3 用例（真实向量化/入库→检索命中/对话装配）；同时修测试隔离（全局串行锁 + DB_PATH 单点 + asyncio.run）。
- #12 的 LLM 摘要压缩为可选增强，暂用字符滑窗。
- **全量回归：`python -m pytest tests/ -q` → 217 passed, 10 skipped, 0 failed**。

## 已知既有问题（非本轮引入，供后续）

- ~~`test_llm.py::test_deepseek_normal_budget` 断言 1024 vs 实现 2048~~ 已于 2026-09-21 修正断言并补 2 个边界用例，现全绿。
- 擦除数据后 `test_retrieval_meta.py` 若干检索断言会 FAIL（数据缺失，非代码回归）；跑全套前先灌回测试文档。
- 真实 e2e 依赖本地 BGE 权重（`data/models/models--BAAI--bge-large-zh-v1.5`）；无权重自动 skip。
