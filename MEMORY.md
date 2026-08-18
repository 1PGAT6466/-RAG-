# MEMORY.md — 长期记忆索引

## 项目背景（2026-08-13）
- 工作目录：`E:\更新RAG框架`（新框架，代号"伏羲"，RAG 工业知识库）
- 旧框架：`E:\RAG伏羲`（v1.50，过度臃肿已废弃，仅作组件移植来源）
- 新项目目标：以 RAG 为核心链路，Agent 内含 Workflow（稳定子任务固化），Workflow 节点可回退 Agent；核心 LLM 调度但基础链路工作流化，提升可靠性。

## 已完成交付（2026-08-13）
- `docs/新旧框架对照表.xlsx`（3 sheet：功能对比/移植组件清单/建议舍弃）
- `docs/组件移植清单.md`（P1/P2/P3 组件移植清单 + Agent-Workflow 落位建议）

## P1 移植已落地（2026-08-13）
- `src/retrieval/rerank.py`：Rerank 三级降级链（SiliconFlow BGE-Reranker → DeepSeek 打分 → 本地 TF-IDF+jieba），字段适配 content/text
- `src/retrieval/ranking.py`：动态融合权重 + 精确型号匹配 + 分类加权（连接器领域型号正则）
- `src/retrieval/search.py` 已接入：动态 α 融合 → post_rank → rerank
- Feature Flag（.env）：`RAG_DYNAMIC_RANKING=1`、`RAG_RERANK=1`（可置 0 回退硬编码权重）

## 关键环境事实
- 已装依赖：chromadb 0.6.3、jieba 0.42.1、aiohttp、bcrypt、openpyxl
- .env 含真实密钥：SILICONFLOW/DEEPSEEK/MIMO（注意安全，勿外泄）
- 本地模型：data/models 下有 bge-large-zh-v1.5、bge-small-zh-v1.5
- 新框架 chunk 文本字段是 `content`（非 old 框架的 `text`）

## 待办（后续）
- P1 剩余：ChromaDB 替换 SQLite 向量暴力扫描、jieba 替换手写 bigram
- P2：语义分块、查询扩展、事实性校验、bcrypt+RBAC

## 产品方向决策（2026-08-13 用户确认）
- 图谱：实体关系图 + 文档引用图（两者都要）
- 文件管理：字段筛选 + 目录浏览（混合模式）
- 结论：借鉴 WorkBuddy「数据库式内核」+ Obsidian「认知层图谱」+ 自有 RAG 实体抽取，三层融合
- 蓝图已定：`docs/工业知识库三层架构设计文档.md`（L1基础/L2管理/L3认知 三层 + 5 阶段实施）
- 数据模型核心：新增 entities/entity_chunks/entity_files/entity_relations 四表（实体为本，文件为壳）
- Obsidian/WorkBuddy 均为 Electron 闭源（app.asar），只能借鉴产品形态，无法读源码
- 实施顺序：阶段1(ChromaDB+jieba) → 阶段2(实体抽取+图谱) → 阶段3(管理交互) → 阶段4(双图谱可视化)

## 插件系统决策（2026-08-13 用户确认）
- 时机：现在就定机制（在三层架构内预留插件运行时）
- 形态：既要 Workflow/Skill 型插件，也要 Tool 型插件（两者都要）
- 蓝图：`docs/插件系统设计文档.md`（第 0 层插件运行时）
- 稳定性三支柱：隔离(子进程) + 契约(manifest+api_version) + 生命周期(状态机)
- 关键：插件子进程隔离（旧框架是 importlib 直接 import 进主进程，是不稳定根源）
- 最小内核：Registry + Lifecycle + Host 三组件，不做旧框架的 plugin_analyzer/auto_integrator 等过度设计
- 前端插件页（2026-08-13 补充）：侧边栏新增"插件"入口 + PluginsView 插件中心
- 前端核心：manifest 声明驱动 UI 自动渲染（SchemaForm 通用渲染器），不为每个插件手写页面
- 前端新增：PluginsView.vue + SchemaForm/SchemaResult/PluginPanel 组件 + plugins store/api
- 配套后端 API：list/invoke/enable/disable/uninstall/status

## 插件内核已落地（2026-08-13 阶段最小闭环）
- `src/plugins/`：registry.py + lifecycle.py + host.py + __init__.py
- registry：SQLite 持久化（data/plugins.db）
- lifecycle：状态机 installed→enabled→disabled→uninstalled，api_version 严格校验
- host：子进程隔离 + JSON-RPC + 超时 kill，崩溃自动重启
- 已修 Windows 坑：select 不能用于 pipe（WinError 10093），改用线程读 + join 超时
- example-plugin 验证通过：echo/add/crash隔离/hang超时 全通过
- server.py lifespan 已接入 init_plugins + stop_all
- 插件后端 API 已落地（2026-08-13）：src/api_plugins.py，7 个端点
  端点：GET /api/plugins、GET/{name}、POST/{name}/enable/disable/uninstall/invoke、GET/{name}/status
- 端到端 HTTP 验证通过（TestClient）：列表/启用/echo/add/crash隔离/停用/停用后调用 全通过
- 重要坑：src/api.py(文件) 与 src/api/(目录) 同名冲突，包优先级高于模块。已改：插件路由放 src/api_plugins.py，删除误建的 src/api/ 目录
- JWT_SECRET 已从 16 字节升级到 64 字节（修复 InsecureKeyLengthWarning）
- 待办：前端插件页（SchemaForm 声明式渲染）、Workflow hook 打通

## 前端插件页已落地（2026-08-13）
- 新增：frontend/src/views/PluginsView.vue（插件中心）、components/SchemaForm.vue、components/SchemaResult.vue、api/plugins.js、stores/plugins.js
- router 新增 /plugins，侧边栏新增"插件"入口（MainLayout）
- 构建验证通过（npm run build，PluginsView 已编译产出）

## 重要坑（FastAPI + 启动方式）
- FastAPI 0.141 + Starlette 1.4 的 include_router 是懒加载，子 router 不会立即展开，导致插件路由注册后访问 404
- 已改：api_plugins.py 从"子router+include_router"改为 register(router) 直接挂载，20 路由全部生效
- 启动方式：python -m uvicorn server:app 会有模块路径问题导致加载旧代码，改用 python server.py（或 cd 后 uvicorn server:app）
- 真实业务验证通过：登录/插件列表/启用/echo/add/文档列表/检索(真实走siliconflow rerank)/停用 全通过

## Workflow hook 已落地（2026-08-13）
- 机制：manifest 新增 `hooks` 字段，声明事件→方法映射；`src/plugins/hooks.py` 调度器遍历 enabled 插件触发
- 事件：`on_ingest`（入库后）、`on_search`（检索后）；主链路在节点后调 `run_hook`/`run_hook_async`
- 三原则：非阻塞（executor 异步）+ 可降级（单插件失败仅告警不阻断）+ 声明式（无需改主链路硬编码）
- host.py invoke 已加 timeout 参数；hook 默认 5s 超时
- 示例插件：plugins/hook-demo（kind=workflow，挂 on_search 改写结果 + on_ingest）
- 端到端验证通过：on_search 给结果加 _hook_source 标记；on_ingest 入库后触发无报错

## OCR 引擎升级（2026-08-17）
- 新增 `src/pipeline/ocr_engine.py`：统一 OCR 工厂 `build_ocr(det_cap)` + `ocr_page_text(engine, img)`，兼容两代输出
- 决策：轻量化模型方案落地——**PP-OCRv6 small 优先，回退 PP-OCRv4 mobile**
  - 速度：v6 small 0.689s/页 ≈ v4 mobile 0.722s/页（略快或持平）
  - 准确度：v6 表格/公式页显著更好（页500 990字 vs 862字 +15%，σb/δ 等希腊字母正确）
- 推理优化（已固化到工厂）：use_cls=False（扫描件方向固定）+ rec_batch_num=16，实测提速 27%
- 新包 `rapidocr 3.9.2`（模型 PP-OCRv6_det/rec_small.onnx，DirectML 优先）；旧包 `rapidocr_onnxruntime`（PP-OCRv4 mobile）作回退
- parser.py `_parse_pdf_ocr` + scripts/rebuild_pdf_ocr.py `_build_ocr` 均已改走统一工厂
- 关键坑：新包输出 RapidOCROutput 对象（`.txts` 取文本），旧包返回 (result, elapsed) 元组；`ocr_page_text` 用 try/TypeError 双分支兼容
- 环境事实：本手册无空白页（墨水中位 24.8%），跳页优化无收益；4GB 显存+8 物理核已挤满，多进程无更多收益；本地 OCR 物理天花板 ~2 页/秒，质变靠云端 OCR 或换卡

## 全链路实地验收（2026-08-17，四份真实文件重新入库）
- 四项真实文件重新入库：Foxconn ppt(38 chunks)、Mini-fakra xlsx(4)、标准件新表 xlsx(18)、非标准手册 pdf(1423页流式OCR→~1000 chunk)
- 最终：files=4, chunks=1645, entities=502, relations=4231
- 检索验收通过：连接器焕金层接触电阻≤3mΩ/焕金0.76μm+鎳底层1.27μm；Mini-fakra检测工艺(耐压800V/漏流≤5mA/绝缘≥1000MΩ)；GB/T 3077 20Mn2 抗拉≥785MPa 全答对
- 发现观察点/待优化：
  1. xlsx 解析噪声：`=DISPIMG(...)` 和大量 URL 混入 chunk 内容，需清洗（影响检索质量）
  2. 「标准件新表.xlsx」实际内容是线轨/轴承/立柱/凸轮随动器等外购件，非标准紧固件；用户问「紧固件」召回的是手册目录页（语义 gap，非 bug）
  3. 进度查询接口长轮询会 RemoteDisconnected（uvicorn 断空闲连接），轮询脚本需重连重试，服务本身不崩
- 流式入库实测：1423页 OCR 期间，已索引部分立即可检索（进度31%=500页时已可检索手册内容），验证了「边入库边用」价值

## 三项待优化落地（2026-08-17）
1. **xlsx 噪声清洗**：parser._parse_xlsx 新增 clean_cell——过滤 `=DISPIMG(...)` 图片公式、纯 URL（`^https?://...$`）、空 tab。效果：1478 行噪声归零，型号数据干净（C-MLG12-45/60/80/100），chunk 18→17
2. **进度轮询健壮性**：DocumentsView.vue pollProgress 加 consecutiveFails 计数，单次断线静默重试，连续 10 次（30s）才告警。根因是 uvicorn 断 keep-alive 长连接，服务不崩，重连即可
3. **「标准件」语义 gap**：标准件新表.xlsx 实为外购件选型目录（米思米/怡合达/成都昶拓的线轨/轴承/立柱/凸轮随动器）。_auto_classify 新增「外购件选型」分类（供应商/型号/图片/链接特征优先级最高），重新入库后正确归类，检索召问「供应商/轴承型号」精准命中
- 注意：改 parser/分类后，已入库旧数据需 delete_file + 重新上传才生效
- 全链路验收多项评分见报告（已直接回复用户）：健壮度/稳健度/完善度/自动化/LLM 提取能力均达标，唯一实际问题已归上述三项

## embedder 本地模型重复加载优化（2026-08-17）
- 根因：本地 bge-large-zh-v1.5 权重文件缺失（目录只有 config，无 model.safetensors/pytorch_model.bin），但 _get_local_path 只检查目录存在就返回，导致每次 encode 都重试加载→失败→降级远程。流式入库每批（20页）都白加载一次，拖慢速度+日志藏吹
- 修复：embedder 新增 _LOCAL_UNAVAILABLE + _LOCAL_CHECKED 缓存；_get_local_path 增加 _has_weights() 权重校验（识别 model.safetensors/pytorch_model.bin/分片 safetensors），缺权重直接返回空；本地失败一次后置 _LOCAL_UNAVAILABLE=True，后续直接远程
- 效果：本地缺权重时只告警一次，后续 batch 直接远程不重复加载（实测第2/3次不再打印「加载本地模型」，耗时稳定 0.8s）

## LLM 结构化后处理模型修复（2026-08-17）
- 根因：DEEPSEEK_MODEL=deepseek-v4-pro 也是推理模型（带 reasoning_content），和 MiMo 一样 reasoning 抢占 max_tokens 导致 content 空、反复重试
- 之前记忆「DeepSeek 是非推理、结构化稳定」已过时——deepseek-v4-pro 是推理型，不是 chat 型
- 修复：config 新增 DEEPSEEK_FLASH_MODEL=deepseek-v4-flash（轻量快速非推理），ingest_stages._llm_chat 的 prefer_deepseek 结构化路径优先 flash→回退 pro→MiMo
- 效果：摘/标签耗时从反复重试 40s+ 降到 2.3s/1.6s，JSON 输出干净无空返回
- DeepSeek 可用模型：['deepseek-v4-flash','deepseek-v4-pro']；MiMo 只有推理模型（mimo-v2.5 / pro / asr / tts 共6个），无非推理 chat 型
- 教训：结构化输出（JSON/固定格式）必须用非推理模型，推理型 reasoning_content 会抢占 max_tokens

## 流式入库引擎（2026-08-17 已落地）
- 新增 `parse_pdf_streaming(filepath, on_batch, flush_pages)`（parser.py）：逐页识别，每 flush_pages 页回调一次；自动检测文本层可用性（扫描件/乱码→OCR，否则 fitz 文本层）
- engine.py 重构：`_run` 按 ext 分流——大 PDF(≥RAG_STREAM_MIN_PAGES=20页, `RAG_STREAM_INGEST=1`)走 `_run_streaming_pdf`，否则走 `_run_sync`（原同步链迁到这里）
- `_run_streaming_pdf`：先 add_file 建文件 → 每批 on_batch 里 chunk→embed→add_chunks_batch 追加入库→Chroma 追加→实时 emit 进度 → 全部完成后 sync_chunk_count+classify+extract+_finish_sync(异步后处理摘要/标签/预索引)
- 关键：add_file/add_chunks_batch/chroma add_batch 均为幂等追加，支持多批；chunk_index 全局自增（nonlocal global_chunk_idx）
- Feature Flag：RAG_STREAM_INGEST(默认1)、RAG_STREAM_MIN_PAGES(默认20)、RAG_STREAM_FLUSH_PAGES(默认20)
- 验证：40页测试 PDF → 2 batch → 2 chunk（index 0,1 连续，chunk_count 同步正确）、实体/分类/摘要/标签/预索引全跑通、检索命中正确
- "先入库再处理"天然满足：同步链完成即置 done，异步 Stage(摘要/标签/预索引)后台慢慢跑
- 注意：大 PDF 走 OCR 时按 RAG_PDF_OCR 检测，乱码/扫描件才 OCR（有文本层则 fitz 直读更快）

## 关键 bug 记录（本轮修复）
- db.py add_chunks_batch 用 executemany 的 lastrowid（返回 None）推算 FTS rowid，导致 `NoneType + int` 崩溃、入库全失败
- 已改：改逐条 INSERT 拿真实 lastrowid 再同步 FTS；（此前文档列表那 6 份是旧数据）
- 插件契约铁律：插件 stdout 走 JSON-RPC，**绝不能 print**（会污染协议返回"非法 JSON"）；日志应通过返回值或 stderr
- 入库有两条路径：src/pipeline/ingest.py（同步）+ task_queue.py（异步，实际被 upload API 用）；on_ingest hook 挂在 task_queue._process
- embedder 本地 bge-large-zh 已能正常加载（1024 维，4096 字节/向量）

## 阶段 1 主链路：jieba + ChromaDB（2026-08-13 完成/部分完成）

### jieba 分词替换 ✅ 完成
- 新增 `src/storage/tokenizer.py`：jieba 分词模块
- 替换 `_to_fts_query`（bigram → jieba 短语查询）、`add_chunk`/`add_chunks_batch`（FTS 入库用 jieba）
- Feature Flag：`RAG_JIEBA=1`（默认开启，置 0 回退 bigram）
- 迁移脚本：`scripts/migrate_stage1.py`（jieba FTS 重建 + Chroma 向量写入）
- 验证：4 组测试查询全部命中正确文档（连接器/镀金层/Mini-FAKRA/PA66+GF30）

### ChromaDB 向量存储 ✅ 已修复（2026-08-13）
- 新增 `src/storage/chroma_store.py`：Chroma 向量存储模块（add/search/delete/count/ensure_synced）
- 接入 `search.py` 的 `_vector_search`（Chroma 优先，回退 SQLite 暴力扫描）
- 接入 `task_queue.py` 和 `ingest.py` 的入库/删除流程
- **根因分析**：Chroma 0.6.3 的 `PersistentLocalHnswSegment` 在数据量超过 `sync_threshold`（默认 1000）时触发 `_persist()`，此时 hnswlib 的 `persist_dirty()` 在 Windows 下写出的 HNSW 索引文件（`index.bin`）损坏/缺失，但 `index_metadata.pickle` 已写入。重启后 `_init_index` 读到 pickle 认为索引已持久化，调 `load_index` 找不到 bin 文件 → `Cannot open header file`。
- **修复**：collection metadata 设置 `hnsw:sync_threshold=1000000000`（极大值），永不触发 `_persist()`。数据靠 `chroma.sqlite3` 持久化，重启时从 sqlite 重建内存 HNSW（数据量小，重建开销可忽略）。
- **验证**：3 次重启 + 增量写入新文档 + 跨进程检索，全部通过，日志无 header 报错。
- Feature Flag：`RAG_CHROMA=1`（已启用）

### 阶段 1 总结
| 项 | 状态 |
|---|---|
| jieba 替换 bigram | ✅ 完成 |
| ChromaDB 向量存储 | ✅ 已修复（sync_threshold 极大值绕过 Windows HNSW bug）|
| FTS 数据迁移 | ✅ jieba FTS 重建完成（1546 条）|
| 端到端检索验证 | ✅ 4 组测试全通过 |
| 跨进程重启验证 | ✅ 3 次重启 + 增量写入全通过 |

## 阶段 2 实体抽取 + 图谱（2026-08-14 后端闭环完成）
- 新增 `src/extraction/entity_extractor.py`：规则抽取（保底）+ LLM 抽取（增强），双通道降级
- 新增 `src/extraction/relation_builder.py`：实体入库 + 同 chunk cooccur 建边 + 文档相似度建边
- 新增 4 表：entities / entity_chunks / entity_files / entity_relations（实体为本，文件为壳）
- 接入 task_queue `_process`（入库后触发，Feature Flag 控制，失败不阻断）
- API 端点：`GET /api/entities/graph`（实体图谱）、`GET /api/entities/{id}`（实体详情）
- 验证：3 份真实文件 → 108 实体 / 2707 边，图谱 API 返回正常

### B 部分：实体质量+LLM异步化已落地（2026-08-14）
- **实体规范化 `normalize_entities`**：系列归并（MLG12-45→MLG12，规格入 attributes.variants）+ 泛化词过滤（连接器/设计流程等停止词）。实体从 108 个（95 噪声 connector）降到 18 个干净实体
- **LLM 异步化 `llm_worker.py`**：入库同步阶段只跑规则抽取（零 LLM，基础图谱立即可用），LLM 抽取丢到后台 ThreadPoolExecutor 异步跑（不阻塞 task_queue 队列线程）
- 关键坑：线程池线程内 `asyncio.run` 在多线程下报 `cannot schedule new futures after interpreter shutdown`；已改同步版 `extract_llm_sync`（httpx.Client），彻底避开 asyncio
- **成本控制**：LLM 抽取实测 ~100s/chunk（MiMo 推理型），加 `RAG_ENTITY_LLM_MAX_CHUNKS=50` 上限 + `RAG_ENTITY_LLM=0` 默认关（按需开）
- Feature Flag 新增：RAG_ENTITY_LLM / RAG_ENTITY_LLM_WORKERS / RAG_ENTITY_LLM_INTERVAL / RAG_ENTITY_LLM_MAX_CHUNKS

### 实体抽取关键坑（本轮踩坑）
- **泛化型号正则 `\b[A-Z]{2,5}[- ]?\d{2,4}\b` 在表格数据上疯狂误伤**（Page10、ZD420、DSC335、UL-94 全被当连接器，标准件表曾抽出 481 个噪声 connector）
- **修正**：连接器型号改「白名单词典 CONNECTOR_DICT + 明确系列前缀正则（MLG\d{1,3}）」，去掉泛化正则
- 材料词典补充：PA46/PA9T/PA10T/PA6T、C7025/C7035 铜合金
- **中文边界 \b 问题**：`C7025和C7035` 中 \b 在中文和字母间不成立（未解决，低优先级）
- **LLM 推理模型坑**：deepseek-v4-pro 和 mimo-v2.5 都是推理型，会先把大量 token 花在 reasoning_content 上，max_tokens 太小会导致 content 为空/截断。实体抽取的 max_tokens 需≥4096（MiMo reasoning 常达 3000-4000 token）；且需 system prompt 明示「不要思考过程」
- **MiMo 域名已修复**：正确域名是 `https://token-plan-cn.xiaomimimo.com/v1`（tp-... 密钥属 Token Plan，cn 区域）。旧配置 `api.mimo.com` 不存在。config.py 默认值也已改正确。MiMo 已能正常抽取（含 param 类型），DeepSeek 降级链路保留

### 待决策/待办（阶段 2）
- ~~实体规范化~~ ✅ 已做 MLG 规格合并 + 泛化词过滤（normalize_entities）
- ~~LLM 抽取异步化~~ ✅ 已做 llm_worker 后台线程池 + 规则同步兜底
- ~~前端可视化~~ ✅ 已做 GraphView 双图谱 Tab（文档引用图 + 实体关系图 + 反链面板）
- 泛化词过滤、系列归并已落地，实体从 108 降 18（质量提升）

### A 部分：前端双图谱可视化已落地（2026-08-14）
- 重写 `frontend/src/views/GraphView.vue`：双图谱 Tab 切换（文档引用图 / 实体关系图）
- 实体图：节点按 type 着色（connector蓝/material绿/standard橙/process紫/param粉），节点大小=degree，点击打开反链面板（drawer）
- 后端增强：`get_entity_graph` 支持 types 过滤 + 节点带 degree；`/api/entities/graph` 接受 etype 参数
- 反链：`/api/entities/{id}` 返回 entity + files + chunks，前端展示关联文档表 + 片段列表
- 前端 build 通过（GraphView 68.78 kB）

### ⚠️ 重要坑：端口 8099 频繁被占
- 多次启动服务报 `Errno 10048`（端口被占），根因是历史遗留多个 python 服务进程（PID 6176/3036/22628等）抢占端口
- 解决：`Get-NetTCPConnection -LocalPort 8099 -State Listen` 找到 OwningProcess 后 `Stop-Process -Force` 逐一 kill
- 另：`process kill` 杀的是 wrapper 进程，实际 python 子进程可能还活着，需用 `Get-Process python` 查全再杀

## 阶段 3 管理交互（2026-08-14 进行中）
- 后端：`list_files_with_entities(category/model/material)` 新增，聚合每文件的型号（connector）+材料（material）实体；`/api/documents` 接受 model/material 筛选参数
- 前端 DocumentsView：新增型号/材料筛选栏（el-select，可选）+ 文件卡片展示型号/材料标签（tag-model蓝/tag-material绿，样式在 global.css）
- 全局样式：新增 `.tag-model`（蓝）、`.tag-material`（绿）
- 测试数据：重新上传 137MB《非标准机械设计手册.pdf》（1423 页 → 1575 chunk），本地 bge-large CPU 向量化极慢（>10 分钟），靠后台队列不阻塞
- 待办：日期/状态筛选、列表↔表格视图切换（设计文档 §4 多维表格卡片）

### 阶段 3 剩项已补齐（2026-08-14）
- DocumentsView 新增：日期筛选（近7/30/90天）+ 表格↔卡片视图切换（el-table 多维表格 + el-radio-button 切换）
- 表格列：文件名/分类/型号(el-tag)/材料(el-tag)/块数/大小/更新时间，行点击跳详情
- formatDate 函数处理 SQLite datetime 格式；.clickable-row 样式

### ⚠️ 性能问题（2026-08-14）：本地向量化大文档极慢
- 137MB PDF → 1423 页 → 1575 chunk，本地 bge-large-zh CPU 向量化 >40 分钟仍未完成
- 根因：SentenceTransformer.encode(1575块) 一次性批量，CPU 上 batch_size=32 循环 50 次，极慢
- 设计文档 §9 只见「后台队列不阻塞 API」，未解决「太慢」本身
- 待优化方向：①改 SiliconFlow API 远程向量化（快且免费配额）；②CPU 上 batch 分批+进度回调；③降维/轻量模型 bge-small

### 混合向量化方案 C 已落地（2026-08-14）
- embedder 重写：大 batch（>300 chunk）自动切 SiliconFlow 远程，小 batch 本地模型；远程失败降级本地
- 修复 bug：原 `_get_local_path()` 硬编码 large 路径、忽略 EMBEDDING_MODEL 配置；`SILICONFLOW_API_KEY` 原用 os.getenv 读不到（独立进程不加载 .env），改成 config 变量
- 实测：305 块远程向量化 4.2s（vs 本地 40+ 分钟）；维度统一 1024（large），与现有 ChromaDB 数据兼容
- 新增 feature flag：EMBED_REMOTE_THRESHOLD=300（config.py 已加 SILICONFLOW_API_KEY、EMBED_REMOTE_THRESHOLD）

### standard 标准号去重规范化已落地（2026-08-14）
- 新增 `_normalize_standard_name`：清洗换行（GB/T\n157→GB/T 157）+ 归并年份（GB/T 699-1999→GB/T 699）+ 过滤碎片（数字<2位丢弃）
- 正则改 `[\s\n]*` 支持跨换行匹配（PDF 中标准号常被拆行）
- 效果：standard 实体 409→320（去重 89 个），零换行碎片零重复主体，全为真实国标号

### param 规格卡已落地（2026-08-14）
- 规则抽取新增 `PARAM_PATTERNS`：阻抗/频率/额定电压/额定电流/温度范围/插拔寿命/接触电阻/镀层厚度，带 value+unit 结构化属性（温度范围特殊处理 -40~105℃ 存范围）
- 文件级规格关联 `_build_file_spec_edges`：同文件 connector×param 建 rel_type="spec" 边（因分块会拆开型号与参数，chunk 级 cooccur 关联不到）
- db 新增 `get_entity_spec_params`（spec 边反查 param）；API `/api/entities/{id}` 返回 spec_params
- 前端 GraphView 反链面板新增「规格参数」卡（点 connector 显示参数表格，specValue 拼接 value+unit）
- 效果：351 实体（新增 5 param）、20 spec 边，M12 规格卡展示 5 参数
- ⚠️ 精度瑕疵已修（2026-08-14）：`_build_file_spec_edges` 改邻接窗口版（window=100，用 chunk_index 最小距离），param 与 connector 仅位置接近才建 spec 边
- 效果：M12→3 参数（镀层厚度/插拔寿命/频率），M8→5 参数，MTD/USB/MLG 系列→0（附近确实无参数，正确）
- spec 边 20→8 条，全部对应真实相近位置（最小距离 2~49）

## 任务 BA 交付（2026-08-14）
- A 规格卡精度：已修（见上，邻接窗口 window=100）
- B 语义关系边：新增 `build_semantic_edges`（relation_builder.py），三类语义边
  1. compatible_process（material→process）：`MATERIAL_PROCESS_MAP` 知识表驱动（不锈钢→热处理/焊接/电镀/冲压等），17 条
  2. standard_category（standard→领域）：`classify_standard` 前缀规则，存进 entities.attributes.category（202/320 分类，机械制图90/材料68/工艺21/紧固件16/国际5/德标2），不入边
  3. uses_standard（material→standard）：`_build_uses_standard_edges` **严格同句共现**（按句切 chunk content）
- db 新增 `get_entity_relations`（排除 cooccur，带 direction/other_name/other_type），api `/api/entities/{id}` 新增 relations 字段

## 关键教训（2026-08-14）
- **uses_standard 用 chunk 级共现会严重误导**：不锈钢被连上 47 个标准（密度表/紧固件表挤同 chunk），改严格同句共现后降到 1 条（GB/T 1220，真实：材料牌号对照表里 1Cr18Ni9Ti 不锈钢→GB/T 1220）
- **PDF OCR 质量差**（非标准机械设计手册.pdf 有大量乱码如"因东专业标准.JtJii"），抽取前清洗很重要；同句共现在 OCR 差时反而比 chunk 级更稳健
- 知识库语义边不能靠盲目共现，要“领域知识表 + 严格同句”双管齐下

## 标准分类 100% + 前端语义边（2026-08-14）
- 标准分类覆盖率 63%→100%（320/320）：补充紧固件 8xx/9xx + 两位数 10-99 规则，并加 12xx/15xx 材料号排除规则（GB/T 1220/1298/1591 是材料非机械制图）
- 最终分布：紧固件 134 / 材料 96 / 机械制图 62 / 工艺 21 / 国际 5 / 德标 2
- 边界精度：4xxx 区间仍有少量材料号（GB/T 4172 耐候钢/4423 铜棒）被归机械制图，规则分类粗粒度，要 100% 精确需 LLM 或完整标准库
- 前端 GraphView：边按 rel_type 着色（cooccur灰/spec青/compatible_process绿/uses_standard橙）+ 图例分「节点/关系」+ 双重筛选（实体类型 + 关系类型）+ 反链面板新增「语义关系」卡（带方向箭头）
- db `get_entity_graph` 补返回 attributes 字段（否则 standard 节点 category 拿不到）
- 构建通过（npm run build 4.98s）

## LLM 精分类标准号（2026-08-14）
- 新增 `llm_classify_standards`（entity_extractor.py）：对规则低置信度号段（2xxx-5xxx 宽泛区间）批量调 LLM 分到 10 类领域
- 关键坑：一次发 55 个号给 LLM 会因输出长/推理 reasoning_content 超限制被截断返回空，改**每批 20 个**循环
- 修正大量误分类：GB/T 5780-5784 六角螺栓（规则误归工艺→紧固件）、GB/T 3098 紧固件性能→紧固件、GB/T 4140/4172/4423/4454 材料、GB/T 4942 电机防护→电工、GB/T 2685/2686/2688 轴承、GB/T 3452/3277 密封件、GB/T 3478 花键→公差配合
- 新增 `standard_categories` 缓存表（db.py），`get_standard_category`/`set_standard_category`，LLM 结果持久化避免重复烧 token；relation_builder 分类顺序改为缓存>规则>LLM
- 最终分布：紧固件 149 / 材料 84 / 机械制图 60 / 轴承 6 / 公差配合 5 / 密封件 4 / 工艺 3 / 电工 2 / 国际 5 / 德标 2
- LLM 调用封装（MiMo优先降级DeepSeek）：MIMO_MODEL=mimo-v2.5，有 reasoning_content 字段

## 前端 standard 按 category 细分着色（2026-08-14）
- GraphView 改：standard 节点按 attributes.category 细分着色（紧固件橙/材料红/机械制图青/轴承黄绿/密封件深蓝/公差配合金黄/电工粉等，STD_CATEGORY_COLORS 10 色）
- 图例和筛选拆成 standard 细分（标准·紧固件 等），普通类型保持（连接器/材料/工艺/参数）
- 筛选改本地过滤：fetchGraph 改为全量拉取（去 etype 参数），新增 allNodes + applyNodeFilter（std:xxx 前缀匹配 category），applyEdgeFilter 只保留两端节点都在的边
- watch 改：activeTypes→applyNodeFilter（不再重新 fetch），activeRelTypes→applyEdgeFilter
- 新增 nodeCategory/nodeColor/nodeLabel 辅助函数；构建通过

## 入库引擎核心驱动（2026-08-14）
- 放弃散脚本，改为「引擎核心驱动」：新建 `src/pipeline/engine.py`（IngestEngine）+ `src/pipeline/ingest_stages.py`（内置 Stage）
- 引擎设计：6 同步 Stage（parse→chunk→embed→store→classify→extract）+ 3 异步 Stage（summarize→tag→preindex）
- 统一原则：所有触发（HTTP上传/文件夹监控/cron）都只做"发现文件→engine.enqueue()"，不各自实现逻辑
- Stage 可插拔 + `critical` 标记（embed/store 失败则整任务失败，其余降级跳过）；单点失败不阻断
- 三个触发器：HTTP 上传（api.py 已改走 engine）、`scripts/watch_folder.py`（轮询+已处理登记）、cron（后续接）
- 异步后处理默认开启（.env：RAG_AUTO_SUMMARY/TAG/PREINDEX=1），上传后无感生成摘要+标签+预索引
- db 新增 files.summary 列 + `update_file_summary`；`init_db` 用 `_migrate_add_column` 幂等迁移旧表（CREATE TABLE IF NOT EXISTS 不会加列）
- 修了两个真实 bug：
  1. 本地 bge-large-zh 模型权重缺失（只有 config 无 model.safetensors），embedder 加本地失败→自动降级远程 SiliconFlow
  2. MiMo 是推理模型，reasoning_content 会抢占 max_tokens 导致 content 空（finish_reason=length）；结构化输出（tag/预索引）改 `prefer_deepseek=True` 优先 DeepSeek（非推理），且 tag 的 prompt 里 `\"` 转义引号会让模型返回空，已改平文字示例
- `_llm_chat`（ingest_stages）：provider 循环（prefer_deepseek 时 DeepSeek→MiMo），每 provider 重试 2 次应对偶发空返回

## 检索三增选（2026-08-14）——图谱检索导航 + 引用标注 + 精确锚点
- 新增 `src/retrieval/graph_recall.py`：图谱召回模块（第三召回源），把图谱从「可视化」升级到「检索导航」
  - 路径：query 实体识别（复用 entity_extractor 规则，零 LLM）→ get_entity_by_name → get_entity_chunks 反查 chunk
  - 一跳邻居扩展只走强语义边（spec/uses_standard/compatible_process），不走弱 cooccur，避免拉到无关共现
  - 实体类型权重：standard=1.0 > connector=0.9 > param=0.8 > material=0.7 > process=0.5
  - Feature Flag：RAG_GRAPH_RECALL=1（config.py + .env）
- search.py 接入：graph_recall 作为第三召回源，与 BM25/向量同结构，复用 weighted_rrf_fusion 融合 + rerank 精排
  - ranking.weighted_rrf_fusion 加可选 graph 参数（权重与向量同档 v_w），向后兼容
- 引用标注（强制、结构化）：chat/engine.py 重写
  - `_build_reference_context` 把检索结果编成 [1][2][3]（含文档名+chunk_index 段落号），返回 (context_text, refs)
  - SYSTEM_PROMPT 强制 LLM 用 [编号] 引用、不得杜撰编号；编号由代码生成（非 LLM 编造，杜绝假引用）
  - `generate` 返回 tuple (answer, refs)；`build_citation_sources` 从 answer 正则提取实际被引用的 [编号] 反映射
- api.py /api/chat：sources 加 ref/file_id/chunk_id/chunk_index 精确锚点，只返回真正被引用的来源
- db.get_entity_chunks 补选 chunk_index 列（锚点需要）
- 验证：端到端通过——GB/T 3077 落「第250/274段」、Mini-FAKRA 诚实回答无信息不编造、引用标注 [3][4] 正确反查

## 前端引用渲染（2026-08-14）
- ChatView.vue 重写：答案里 [编号] 变可点击脚注（蓝色 sup，点击滚动高亮对应引用卡片）
  - 引用卡片：ref + 文档名 + 段落号（chunk_index）+ 内容片段，点击跳转文档详情
  - 脚注用全局 CustomEvent('jump-source') 桥接（v-html 内联 onclick 无法直接绑 Vue 方法）
- DocumentDetail.vue 支持 ?chunk=N 锚点：跳转到指定段落并高亮（chunkEls 映射 + scrollIntoView）
- 构建验证通过（npm run build，ChatView/DocumentDetail 均已产出新 bundle）

## PDF 扫描件乱码 + OCR 加速（2026-08-17）
- **乱码根因**：`非标准机械设计手册.pdf`（144MB/1423页）是扫描件但文本层内嵌字样 ToUnicode CMap 损坏，PyPDF2/pdfplumber/PyMuPDF 提取都是「同形替换乱码」（每个字是真汉字但语义错），字符级检测无法识别，唯一可靠解是整本 OCR
- **OCR 方案**：`src/pipeline/parser.py` 有 `RAG_PDF_OCR=force`（强制整本 OCR）+ `RAG_OCR_DML=1`（DirectML GPU 加速）；`scripts/rebuild_pdf_ocr.py` 多进程重建已入库乱码 PDF
- **关键性能事实（机器=AMD RX 6600 XT 4GB 独显 + 纯 CPU 16 核）**：
  - RapidOCR 纯 CPU：dpi=100 约 1.7s/页，dpi=150 约 4.35s/页（模型已加载后持续速度）
  - **DirectML GPU：dpi=150 约 1.1-1.4s/页（4 倍提速）**，1423 页 ≈ 35 分钟
  - **多进程在 Windows 上负收益**：RapidOCR 的 ONNX 推理即使设 intra_op_num_threads 也会吃满全核，多进程争用反而更慢（8进程 17s/页 vs 单进程 1.7s/页）。OC 场景永远用单进程
  - DPI 降到 100 几乎不提速（瓶颈是 det+rec 网络固定推理开销，非图像尺寸），但质量仍可接受
- **numpy 版本坑**：装 rapidocr 时把 numpy 从 1.26.4 升到 2.4.6；onnxruntime（原版）与 onnxruntime-directml 不能共存（都提供 onnxruntime 包），需先卸载再装 directml 版（当前 1.24.4）
- **Rebuild 脚本重构**：`_ocr_page` 原每页 `fitz.open(pdf_path)` 重新打开 144MB PDF（巨大开销），改为 `initializer` 每进程 open 一次 doc + 加载一次模型（`_WORKER` 全局缓存）
- **Embeddable Python 坑**：`python -c` 模式下 cwd 不进 sys.path（sys.path[0] 是 python311.zip 而非 ''），需 `sys.path.insert(0,'.')` 或设 PYTHONPATH
- RapidOCR 参数：`RapidOCR(det_use_dml=True, rec_use_dml=True)` 启用 DirectML；config 里 det/rec 均有 use_dml 字段
- **Embedder 400 bug（2026-08-17 已修）**：SiliconFlow bge-large-zh 单条 input 超长（>~512 token）返回 400 code 20015「parameter invalid」。CHUNK_SIZE=800 字，OCR 出的表格 chunk 超长触发。修复：`_encode_remote` 对每条文本截断 `t[:400]`（bge-large max_seq_length=512）
- **onnxruntime-directml 1.24.4** 安装成功 → DML provider 可用；机器有 AMD RX 6600 XT 4GB（OrayIddDriver 是虚拟显示器，别误判）
- **rebuild_pdf_ocr.py 已被并行重写为 checkpoint 版**（2026-08-17）：`_shard_worker` spawn 多进程各持 DML 会话 + 每页实时写 `data/ocr_ckpt/*.jsonl` 断点续跑 + OCR 先行（全成功才清库）+ `--det-cap 960` 提速。不要用我早期手改的 initializer 版（无 checkpoint，崩溃丢结果）
- **重建实测（2026-08-17）**：2 进程 DML GPU + det-cap 960 → 0.86 页/秒（单进程 0.5，提速 1.7x）；1423 页 OCR 1648 秒；远程向量化 1534 块 15 秒；实体重抽 1346 实体 + 15 spec 边
- **OCR 公式页局限**：热力学公式表（希腊字母/上下标/数学符号）OCR 仍有「do>>b0>u」类乱码，但正文/术语/标准号/表格全部准确。这是公式非纯文本的固有限制，对文本检索影响小
- **这台机器 OCR 最优配置（2026-08-17 实测，已固化为脚本默认）**：
  - **4 进程 GPU（DML）+ det-cap 960 = 1.49 页/秒（1423 页 16 分钟）**，是这台 RX6600XT 的最优
  - 2 进程 GPU = 0.86 页/秒；单进程 GPU = 0.5-1.4 页/秒；纯 CPU 任何进程数都 <1 页/秒（8 物理核，ONNX 多进程争用负收益）
  - 关键发现：瓶颈不是 CPU 核数（实际只有 8 物理核，16 是超线程），是 GPU 会话并行；4 进程挤满显存+GPU 计算
  - 物理 CPU 只有 8 核（AMD Ryzen 7 5700X），GPU 才是提速杠杆
- 脚本默认已改：workers=4 + det-cap=960（rebuild_pdf_ocr.py main argparse）

## 上传链路全自动乱码检测（2026-08-17）
- **parser.py auto 模式重写**：文本层提取 → 双重检测 → 自动分流，无需人工干预
  - 检测 1（扫描件）：文本层总字数 < 页数×30 → 自动 OCR
  - 检测 2（CMap 乱码）：`_text_garbled_check` jieba 多字词命中率「低簇判定」——全文与 24 段×800 字，取最低 4 段中位 < 0.45 判乱码
  - 实测分离度：乱码手册低簇 0.37-0.48；OCR 后正常文本低簇 0.52+，阈值 0.45 余量充足
  - **检测踩坑**：虚拟词频/虚词覆盖率/jieba 平均词长均不可靠（同形替换乱码在字符统计上与正常中文无差别）；只有「多字词命中率低簇」可靠，因为随机汉字不成词
  - 人工构造「循环重复乱码」测试样本会误判（jieba 切出假词），必须用真实乱码文本验证
- 上传链路端到端：扫描件/乱码 PDF → 自动 GPU OCR（单进程 ~1.3s/页，走后台 task_queue 不阻塞）；超大 PDF（千页级）建议 rebuild 脚本 4 进程（1.49 页/秒）
- 服务已重启生效（PID 21916，登录 200）

## 文件夹监控自动入库（2026-08-17）
- 已落地工作流：监控目录 `E:\更新RAG框架\watch_inbox`，丢文件即自动入库（轮询 10s）
- 启动命令：`python scripts/watch_folder.py --dir E:\更新RAG框架\watch_inbox --interval 10`（后台常驻）
- 已处理登记：目录内 `.watched.json`，避免重复入队；支持 pdf/txt/md/docx/xlsx/ppt/pptx
- 全链路实测：M12 规范 .md 投放 → 82 秒完成入库（解析/分块/向量化/实体 15 个+3 spec 边/AI 摘要/AI 标签 8 个/预索引 4 问）→ 可检索带引用锚点
- 注意：watcher 是独立进程，与 server 并发写 SQLite/Chroma（短事务，实测无锁冲突）；deepseek 偶发空 content 重试正常现象
- 服务进程：PID 9048（uvicorn）；watcher 进程：PID 20500

## OCR 推理参数优化（2026-08-17 实测）
- `use_cls=False`（跳过方向分类器，扫描件方向固定，cls 是白跑）+ `rec_batch_num=16`（默认 6，加大识别批深度减少 GPU 调用）
- 单进程实测：0.986 → 0.722s/页（**提速 27%**，质量无损，字数差异 <0.3%）
- 4 进程并行下预计 ~1.9-2 页/秒（1423 页约 12 分钟）
- 已固化到 rebuild_pdf_ocr.py `_build_ocr` 和 parser.py `_parse_pdf_ocr`（DML 分支）
- 跳页优化无收益：手册无空白页（墨水中位 24.8%），此路不通
- 剩余提速路径只剩：流式入库（边 OCR 边可检索，感知可用时间从 16 分钟提前到 ~2 分钟）、换 NVIDIA 卡（TensorRT/CUDA 生态）、云端 OCR（公开文档）

## OCR 提速改造（2026-08-17 上午）
- 用户反馈 OCR 重建太慢：单进程 DML 实测 ~1.2-1.4s/页，1423 页 ≈30+ 分钟；判断 GPU 是否在工作：CPU ~1.7核 + GPU 32% = DML 正常，CPU 满核 = 没走 GPU
- rebuild_pdf_ocr.py 已重写：①断点续跑（data/ocr_ckpt/f{id}_dpi{dpi}_w{k}.jsonl 每页实时落盘，重跑同命令自动续）②多 GPU 进程并行（默认 --workers 2，spawn 各持 DML 会话交错分页）③OCR 先于清库（中断不丢旧数据）④--det-cap 960 可选提速
- worker 用 os._exit(0) 跳过 DML teardown（避免 onnxruntime-directml 拆卸卡死）；缺页时 sys.exit(1) 不清库
- 新增 scripts/bench_ocr.py（只读基准：g1/g2/g1c960/g2c960 四配置含字数质量对比）+ scripts/_watch_and_bench.py（等 file_id=4 重建完自动跑基准 → data/bench_ocr_result.txt）
- 基准结果出来后回填本节

## 磁盘危机处理 + 本地模型路径修复（2026-08-17 傍晚）

### ⚠️ C 盘磁盘满危机（曾降至 0 字节）
- **根因**：`C:\Users\feng-shaoxuan\AppData\Local\Temp\easyclaw\runtime\deploy-verify` 历史遗留大量工控软件副本（Beckhoff TwinCAT/FANUC/EPSON/AUTOSHOP 等部署验证产生的重复拷贝），多个 `copy\PGAT-*` 节点，规模数十 GB
- **清理手段**：`cmd /c rmdir /s /q <目录>`（普通 Remove-Item 会因路径过长 ENOSPC/卡住，cmd /c rmdir 更省空间且稳定）
- **结果**：C 盘从 0 字节恢复到 42GB 可用；deploy-verify 还剩 263MB 残渣（FANUC 深层路径 >260 字删除不掉，不影响运行）
- **教训**：这台机器 C 盘很紧张，EasyClaw Temp 下的 runtime/deploy-verify 是历史垃圾重灾区；磁盘满会让一切写操作失败（包括 SQLite/Chroma），必须优先释放

### 上传临时文件残留 bug（已修）
- `src/pipeline/engine.py` 的 `_run` 补 finally 块：任务结束后调 `_cleanup_tmp` 删除 `_tmp_` 前缀的源临时文件
- 背景：API 上传大文件先落 `uploads/_tmp_xxx`，`_prepare` 再 copy 成正式文件名，但原 `_tmp_` 文件从不清理，上传 137MB PDF 就会残留 137MB 垃圾
- 新增 `_cleanup_tmp(src_path)`：文件名 startswith `_tmp_` 且存在则 unlink

### 本地 bge-large 向量化修复（重大）
- **根因**：embedder `_get_local_path` 只搜 HF 标准路径 `data/models/models--{dir}/snapshots`（该处只有 config 缺权重），且**缺权重时直接 return "" 提前退出，不会继续搜旧路径**
- **权重实际在** `data/models/models/{dir}/snapshots/master/pytorch_model.bin`（1241MB，旧框架遗留路径）
- **修复**：①标准路径缺权重时不 return，改 logger.warning 继续往下 ②补搜两个旧路径 `models/models/{dir}/snapshots` + `models/models/{dir}/`（带 `_has_weights` 校验）
- **效果**：本地 bge-large 真正可用（1024 维，首次加载 25s，之后 encode 10s/批），小 batch（≤300 chunk）走本地零成本，大 batch 走 SiliconFlow 远程（混合策略 EMBED_REMOTE_THRESHOLD=300 终于真正生效）
- **验证**：新进程 `_get_local_path()` 返回 `data/models/models/BAAI--bge-large-zh-v1.5/snapshots/master`，`_has_weights=True`；encode 2 条成功 1024 维
- 服务重启确认（PID 23952，salty-rook 会话，8099 端口，DB/ChromaDB/插件全部就绪）

## 全系统自主审查（2026-08-18，多技能联合）
- 技能组合：ai-code-review + superpowers + fullstack-dev + vercel-react-best-practices + debug-pro + frontend-design（adversarial-review/adaptive-socratic/multi-agentorchestration 属写作/教学/Node编排，不适用代码审查）
- 已修复（Critical/Warning）：
  1. XSS：ChatView.vue v-html 渲染 LLM 答案无消毒 → 加 DOMPurify.sanitize(USE_PROFILES html)
  2. N+1：search.py _attach_file_names 每结果单查 get_file → 改一次 IN 查询批量映射
  3. 输入校验：api.py 所有请求模型加 Pydantic Field(min/max/pattern) 约束（register/login/search/chat/category/tags）
- 已验证：前端 build 通过、后端语法 OK、服务重启正常、422/401 拦截生效
- 环境坑：npm 全局配置 omit=dev 导致 devDependencies(vite) 不装，需 --include=dev；node_modules 曾被删，已全量重装（vite/dompurify 就位）
- 未修复（需决策，记录待办）：
  1. JWT 存 localStorage（易被 XSS 窃取）→ 应改 httpOnly cookie，但需后端 session/refresh 改造，体量大暂缓
  2. db.py threading.local 连接不回收，长线程池(llm_worker)会累积连接 → 需加连接池/显式 close 生命周期
  3. 限流只按 client.host，反代后同 IP → 生产需 X-Forwarded-For
  4. plugins/host.py _read_stderr_tail 同步 read 可能阻塞
  5. main.js 全局注册全部 ElementPlus 图标 → 打包偏大(1.2MB)，可选按需引入
  6. GraphView.vue onUnmounted 仅 stop simulation，未清理 drag/zoom/resize 监听 → SPA 内存泄漏

## 对抗式/追问式深挖修复（2026-08-18 第二轮）
把 adversarial-review(参谋挑刺)、adaptive-socratic(追问)、multi-agent(分工) 三个"不适用"技能映射为代码审查机制，挑刺+追问深挖出 3 个真实 bug：
1. **rerank.py 密钥读不到（Critical）**：模块顶层用 os.getenv 读 SILICONFLOW/DEEPSEEK key，但 config.py 才负责 load_dotenv，独立进程/import 顺序下 os.getenv 读不到 .env → Rerank L1(SiliconFlow) 一直静默空降级到 L2/L3。已改：从 config import 全部密钥。
2. **rerank.py 模型名失效（Critical）**：L2 DeepSeek 硬编码 "deepseek-chat"，但实际可用模型是 deepseek-v4-flash/-pro。已改：用 DEEPSEEK_FLASH_MODEL。
3. **chat/engine.py 降级链不完整（Warning）**：generate() 里 DeepSeek 调用在 try 外，MiMo 失败后 DeepSeek 再失败会裸抛 500；且推理模型(如 MiMo)空 content 未处理。已改：DeepSeek 包 try + 空 content 降级提示。
- 验证：4 文件语法 OK，服务重启正常，注册→登录→真实检索(含 rerank) 全链路 200、5 结果。

## 五轮自主检测 + 功能链路/RBAC（2026-08-18 下午）
用户要求：不止代码，还检测功能点/接口/链路，对齐"统一稳定有序简洁"四原则 + "以RAG为核心的服务器知识库管理平台"定位，避免旧伏羲错误。

### 五轮逐层深挖修复的代码问题（共 10 项，全部验证）
1. rerank.py 密钥 os.getenv 读不到 → 改 config import（L1 SiliconFlow 一直静默降级）
2. rerank.py deepseek-chat 模型失效 → DEEPSEEK_FLASH_MODEL
3. chat/engine.py 降级链不完整（DeepSeek 裸抛500 + 空content）→ 补 try + 降级提示
4. ranking.py exact_match_boost 中文 query split() 恒0 → 加 _query_terms jieba 分词
5. engine.py _tasks 无界增长 → _MAX_TASKS=1000 淘汰最旧已完成
6. entity_extractor 两处 DeepSeek 降级用推理型 → 改 flash 非推理模型
7. relation_builder.py 重复 return count 死代码 → 删除
8. llm_worker _done_chunks 无界增长 → 10万上限清空
9. plugins invoke 任意方法调用 → 加 manifest 方法白名单
10. XSS v-html + N+1 attach_file_names + 输入校验（前几轮已修）

### 功能链路检测发现（接口对照）
- 后端 23 个端点 vs 前端调用：发现孤儿接口——DELETE 文档/改分类/改标签/独立 /search 前端无入口
- 已补：DocumentDetail 加"管理"下拉（删除+改分类+改标签），补齐 C/R/U/D 闭环

### 安全硬伤修复（移植清单 2.4 标注"必做"未做）
- 密码哈希 sha256(快) → bcrypt(慢哈希)，旧 sha256 兼容 + 登录透明升级
- role 字段未使用 → 实现 RBAC 权限体系

### RBAC 落地（本次核心功能）
- 账号：admin/admin123（管理员）、user/user123（用户），bcrypt 哈希
- 清理测试残留账号（t*/z*/stg*/review_*/reg_* 共34个）
- 后端：JWT payload 加 role；新增 require_admin 依赖；写操作（上传/删/改分类/改标签/插件启停/调用）全部 require_admin
- 前端：登录返回 role → auth store 存 role；MainLayout 按角色显示菜单（admin 看插件入口，user 隐藏）；router 角色守卫拦截 /plugins；DocumentsView 上传按钮/DocumentDetail 管理操作仅 admin 可见
- 验证：admin 登录 role=admin 全权限；user 登录 role=user 只读+检索+对话，写操作 403

### 技术栈现状（避免旧框架过度设计）
- 未引入 casbin/RBAC 重框架，用简单 role 字段 + require_admin 依赖（2 档权限足够）
- 未引入旧框架 bagua/hypothalamus/evolution 等隐喻抽象

## 第六轮：Feature Flag 统一治理 + 隐患排查（2026-08-18 下午）
用户确认「继续」，聚焦四原则里的「统一」和「简洁」，避免旧伏羲老路。

### Feature Flag 统一（消灭 os.getenv 散落）
- 发现 10 个文件的 RAG_* 开关用 os.getenv(name, default) 散读，default 值两处维护（config.py + 各模块），易不一致
- 密钥已统一走 config（rerank 已改），但 llm_worker 还有 _env_int/_env_float 私有读取器（命名误导：叫 float 返回 str）
- 修复：config.py 补 RAG_ENTITY_LLM_WORKERS/INTERVAL/MAX_CHUNKS 三开关；llm_worker 删除 _env_int/_env_float 改从 config import
- 统一 6 个模块：search/chroma_store/tokenizer/graph_recall/engine/ingest_stages/parser/ocr_engine 全部从 config 导入 RAG_* flag
- 效果：除 _deprecated 死代码外，os.getenv("RAG_*") 归零；全部 10 文件 py_compile + import 验证通过；服务重启回归 health/登录/检索/对话全 200

### 重大隐患发现（待用户决策）
- **项目无 git 版本控制**：.git 目录不存在，只有 .gitignore 文件；backups 目录也空
- 这是「稳定」原则根因级缺口——改动不可逆、无法回滚，也解释了旧框架为何只敢加不敢删（废弃代码堆 _deprecated）
- 证据：src/_deprecated/task_queue.py 是死代码（无 import，README 自称"删除不影响运行"），但没 git 只能留着考古
- 建议：git init + 首提交 + 后续每次修改前 commit，但这属于用户决策，未擅自执行

### 服务日志噪音（无害）
- chromadb 0.6.3 telemetry 每次报 "capture() takes 1 positional argument but 3 were given"（posthog 版本不兼容 bug），无害但扰人，可设 ANONYMIZED_TELEMETRY=False 静默

## 第七轮：功能完善度 / 前后端对齐检测（2026-08-18）
用户要求：检测功能完善度，确保后端功能跟前端对齐，无「空按钮」「空接口」。

### 检测方法（三向对照）
- 后端每个 endpoint → handler 是否真实现（非 pass/硬编码空返回/TODO）
- 前端每个按钮/事件 → 是否真调用后端（非空回调/console.log 占位）
- 后端返回字段 → 前端是否真渲染（非"返回了但前端当空气"）

### 诊断结论（真实问题 2 个）
1. **型号/材料筛选：后端空接口**——后端 list_files_with_entities 已实现 model/material 参数，但前端 DocumentsView 用本地 computed 过滤，没消费后端参数；且 onFilterChange 是空函数（死代码）
2. **日期筛选：假功能**——前端 filterDate 纯本地过滤，后端根本没 date 参数/字段查询，files 表虽有 created_at/updated_at 但 list_files 不支持

### 修复（对齐「统一」原则，消灭前后端重复过滤逻辑）
- 后端：list_files + list_files_with_entities + /api/documents 新增 date 参数（近 N 天，SQL datetime('now','localtime',?) 相对日期）
- 前端 DocumentsView：筛选改为走后端（category/model/material/date 参数），删空 onFilterChange 和本地过滤 computed
- 数据源拆分（用户确认方案）：allFiles（全量，供分类树+型号/材料下拉选项，fetchAllFiles 单独拉）+ files（筛选后列表，fetchFiles 带参拉）
- 验证：date=7d→4份、category=连接器→0份（正确无此分类）、model=MLG12→1份（精确命中）；npm build 通过

### 其余功能点全部真实现（无空按钮/空接口）
- ChatView：send→/chat→answer+引用脚注跳转全真实现；XSS 已过 DOMPurify
- GraphView：双图谱 d3 渲染 + /graph + /entities/graph + /entities/{id} 反链全真实现
- DocumentDetail：删除/改分类/改标签（ElMessageBox+api+错误处理）全真实现
- PluginsView：启停/卸载/调用 + SchemaForm 声明式渲染 + SchemaResult 全真实现

## 第八轮：分类标准一致性检测与统一（2026-08-18 用户问「分类标准是否一致」）
答案：不一致，存在 3 套互相打架的分类体系（违背「统一」原则）

### 诊断：三套分类体系
1. 文档分类 ingest._auto_classify（写 files.category）：外购件选型/设计手册/材料选型/连接器/标准件/工艺规程/测试报告/未分类
2. 查询分类 ranking._CATEGORY_KW（检索加权）：连接器/机械设计/材料选型/工艺规程/标准件/品质管理/电气自动化
3. 标准号领域 entity_extractor.STANDARD_CATEGORY_RULES（实体 attributes.category）：基础标准/电工标准/德国标准/汽车标准/国际标准/材料/机械制图/工艺/紧固件

### 不一致点（同义不同名/维度混乱）
- 「测试报告」vs「品质管理」、「设计手册」vs「机械设计」、「标准件」vs「紧固件」同义不同名
- 体系③维度完全不同（按标准来源/前缀分，非内容题材），却挤进同一个 category 世界观
- 前端的 STD_CATEGORY_COLORS（有轴承/密封件/公差配合/电工）与后端规则输出（电工标准/基础标准/汽车标准）也对不上

### 修复（用户选定「统一①②为单一词典」方案）
- 新增 src/classification.py：单一权威题材词典 CATEGORY_DICT（8类：外购件选型/连接器/材料选型/工艺规程/标准件/机械设计/品质管理/电气自动化）
- _auto_classify 与 _CATEGORY_KW 统一指向 CATEGORY_DICT（is 同一对象，真共享）
- 体系③「标准号领域」重命名为 standard_domain（隔离自文档题材分类），键名从 attributes.category 改为 attributes.standard_domain
- 标准领域词表统一到 STANDARD_DOMAINS（材料/紧固件/工艺/机械制图/电工/轴承/密封件/公差配合/基础标准/其他），规则与 LLM 共用同一套
- GraphView STD_CATEGORY_COLORS 重写，与后端 STANDARD_DOMAINS 一一对应
- 新增 scripts/migrate_classification.py 幂等迁移脚本

### 数据迁移 + 验证
- files.category：设计手册→机械设计（2个文件）
- 467 个标准实体回填 standard_domain，分布：紧固件214/材料117/机械制图97/轴承12/其他10/工艺5/公差配合4/基础标准3/密封件3/电工2
- 检索/对话/图谱回归全 200

### 教训
- 三个模块各写各的 keyword 表 = 分类标准分裂，检索加权时 query 判「品质管理」而文档标「测试报告」会悄悄失效
- 「单一权威词典 + 常量文件」是消除分裂的正解；标准来源（IEC/DIN/ISO）是另一维度，不该混进内容领域

## 第九轮：枚举/类型维度一致性 + 空功能检测（2026-08-18 用户「继续」）
延续第八轮，扫除分类之外其它维度的隐性分裂和空功能。

### 发现 1：标准领域 rel_type 命名残留（非 bug）
- relation_builder 注释/result dict 一直提「standard_category 边」，但代码从未插入过这类边
- 标准领域实际是实体属性 standard_domain（上轮已改），不是边；result 键名误导

### 发现 2（核心）：两个「孤儿函数」从未接进入库链路
- build_semantic_edges（compatible_process + 标准领域 + uses_standard）和 build_document_similarity_edges（similar）全项目只有定义、无调用
- 后果：
  - 新上传文件永不构建 compatible_process/标准领域/uses_standard 语义边（仅上轮手动跑过一次）
  - links 表永远为空 → GraphView「文档引用图」是空图（只有节点无 similar 边）
- 修复：新增异步 Stage semantic/docsim，入库后后台幂等构建；build_document_similarity_edges 支持 file_id 增量；新增 RAG_AUTO_SEMANTIC/RAG_AUTO_DOC_SIM flag
- 验证：similar 边 0→6，文档引用图 4 节点 6 边

### 发现 3：uses_standard=0 是正确行为，非 bug
- _build_uses_standard_edges 用「严格同句共现」精筛，chunk 558 表格数据里「不锈钢」和「GB/T 5782」不同句（表格相邻），正确过滤掉虚假关联
- 82 个同 chunk 候选对全部被逐句精筛过滤，印证「坑5 chunk级共现误导」的 v2 修复有效

### 实体 type / 关系 rel_type 前后端一致性（确认无分裂）
- 实体 type 5 类（connector/material/standard/process/param）前后端一致（TYPE_LABELS 对应）
- 关系 rel_type 实际 4 类（cooccur/spec/compatible_process/uses_standard）+ links.similar，前端 REL_LABELS 对应
- 边界：entity_relations 有 3 类（cooccur/spec/compatible_process），uses_standard 因数据无同句共现为 0，similar 在 links 表

### 教训
- 「函数写了但没接进调用链」是比「空接口」更隐蔽的空功能——代码 review 只看函数不追溯调用点就漏
- 建完功能必须验证「数据真的产生」：links 表 0 条 = 文档引用图空图 = 功能假象

## 第十轮：全量「函数→调用点」追溯审计 + 死代码清理（2026-08-18 用户「需要」）
延续第九轮，建立完整清单彻底根除隐性空功能/孤儿函数。

### 方法
- AST 自动扫描 src 全部 .py 的顶层函数/类定义（约 200 个）
- 收集全部 Call 名，交叉追溯「定义了但零同名调用」的孤儿
- 人工甄别假阳性：装饰器注册的 stage（register_stage 用 fn 变量）、API handler（router 装饰器）、async worker（threading.Thread target）、parsers dict dispatch

### 确凿死代码清单（已删 18 处）
1. relation_builder: extract_and_store(async) + process_file(async) —— 旧实现，主链路用 process_file_rule
2. entity_extractor: extract_llm(async) —— 只被死代码调，实际用 extract_llm_sync
3. chat/engine: _build_context —— 无编号旧版
4. db: add_chunk/get_chunk/get_chunk_embedding/update_chunk_embedding/update_chunks_embedding_batch/get_links —— 六处孤儿
5. embedder: get_embedding_dim —— 实际用 SentenceTransformer.get_embedding_dimension（复数）
6. parser: _chinese_text_quality（字符级乱码检测，已被 jieba 多字词命中率法取代）/ _fallback_convert / _parse_pdf_with_progress
7. search.py: 未使用的 get_chunk import

### 保留（有真实依赖，不可误删）
- chroma_store.reset/count（scripts/migrate_stage1.py 用）
- chroma_store.ensure_synced（server.py 启动时向量库同步）
- engine.list_tasks（任务查询预留）
- _parse_pptx/ppt/xlsx/docx/txt（parsers dict dispatch）
- 插件生命周期 get_enabled_tools/is_enabled/alive/stop_all（server.py 用 stop_all）

### 关键教训
- 装饰器/线程/事件驱动会让 AST 静态分析产生假阳性，必须结合「谁真正调用 + 是否产生数据」人工甄别
- 「函数被淘汰但没删」是死代码主要来源：_chinese_text_quality 就是被 jieba 多字词法替代后的残留
- 删除前必须验证：①零 AST 引用 ②不在 scripts/ 被引用 ③无同名相似函数混淆（get_embedding_dim vs get_embedding_dimension）
- 结果：8 文件 -181 行净减，全链路回归 200

## 第十一轮：性能 + 异常边界 + 前端 UI 细节（2026-08-18 用户「性能、异常边界、前端UI 都需要」）

### 性能优化
- ranking.exact_match_boost：jieba 关键词分词(_query_terms)提到 for 循环外，消除「每个候选 chunk 重复切 query」的 N 次冗余
- chat/engine.generate：新增 LLM key 前置检查，MIMO+DEEPSEEK 均空时快速失败给出明确提示；MiMo 失败且 DeepSeek 未配时明确报错（不再裸 401 后静默降级）

### 异常边界（关键修复）
- search.py 新增「相关性看门」：BM25 与图谱召回均空 → 直接返回空、不融合不 rerank 不调 LLM
- api_chat：检索空结果快速返回友好提示，避免 LLM 对空上下文编造
- 根因洞察：**向量检索对任意 query（含纯字母乱码）都无条件返回 top_k**，bge-large 对 ASCII 字母串余弦相似度仍达 0.5+，无法用相似度阈值区分「乱码 vs 真实中文查询」（实测真实查询 0.54~0.69 vs 字母乱码 0.53 高度重叠）。唯一可靠判据是 BM25/FTS 词汇级真实命中
- 曾踩坑：先试了 chroma_store.search 加 VECTOR_MIN_SCORE=0.35 阈值，误伤正常查询（「连接器接触电阻」返回0），实测后撤回，改用 BM25/图谱看门

### 前端
- ChatView：renderAnswer 改 computed 缓存（renderedHtml Map），避免消息列表增长后每次渲染重算全部历史 markdown
- DocumentDetail：加载失败显示 error + 重试按钮（原为静默 console.error 永久 loading）

### ⚠️ 重大教训：PowerShell 5.1 中文 query 编码坑
- 用 Invoke-RestMethod 发中文 JSON body 时，PowerShell 5.1 会把 UTF-8 中文破坏成 ???，导致 FTS 检索全 0 条，误以为是服务端 bug 排查了很久
- 正确做法：curl.exe + [System.IO.File]::WriteAllBytes(tmp, [Text.Encoding]::UTF8.GetBytes(body)) + --data-binary "@tmp"
- 服务端其实零 bug，是测试脚本编码问题；但据此事固定了「空结果快速返回」的正确性

### admin 账号密码
- admin / admin123（本轮回归验证用，之前记忆未存，现已补）

## 第十二轮：数据库锁修复 + 并发写事务 + 全局异常可读 + rerank 优化（2026-08-18）

### 数据库锁根因（重要）
- storage/db.py 的 _get_conn 只设了 WAL + foreign_keys，**漏了 busy_timeout**
- 而 plugins/registry.py 的同类 _get_conn **已设 busy_timeout=5000**——「抄对了插件库，漏了主库」
- 多线程并发写（uvicorn async + 引擎 worker 线程 + watcher 独立进程）时，主库遇写锁立即 database is locked（之前日志里出现过）

### 修复
1. db._get_conn 加 PRAGMA busy_timeout=5000（遇锁等待而非立即报错）
2. add_chunks_batch 改显式事务 with conn:（批量提交，缩短写锁窗口；仍逐条取真实 rowid，因 executemany lastrowid 不可靠）
3. server.py 全局异常处理器：数据库锁→「数据库忙请稍后重试」；LLM 未配置→明确提示；不再笼统「服务器内部错误」
4. rerank.rerank_local：tokens 提前 lower 一次 + text.count(t) 合并 in 判断（消除循环内重复 lower/遍历）

### 验证
- 8线程×20次并发写 links = 160 次，零 database is locked（9.4s，busy_timeout 生效）
- 检索/文档列表/实体图谱回归全通过

### 教训
- 同类模块（主库 db.py vs 插件库 registry.py）的 _get_conn 配置要统一，不能各写各的；插件库反而做了正确的 busy_timeout，说明当初是分别开发、未对齐
- SQLite 并发写三件套：WAL + busy_timeout + 显式事务短窗口

## 第十三轮：向量化进度反馈 + rerank 健壮性 + 大图渲染截断（2026-08-18）

### 向量化进度反馈（137MB PDF 干等 40 分钟的核心痛点）
- embedder.encode 新增 progress_cb(done,total) 回调参数，本地/远程均逐批上报
- _encode_local 改分批(batch_size=64)编码，不再一次性塞全部阻塞无反馈；远程原 batch_size=32
- _stage_embed 用 ctx.emit 实时上报「向量化 X/Y 块」；流式路径 on_batch 的 encode 也接回调
- 验证：100 块 → progress_cb 调 2 次(64/36)，单调递增末次 100

### rerank DeepSeek 打分健壮性
- json.loads 前先正则提取 {..scores..}（防 LLM 前导说明/代码块包裹），原直接 json.loads(raw) 会因非纯 JSON 崩溃
- JSON 解析失败/无 scores/空 → 降级返回空（不崩溃）
- scores 元素 float() 加 try（非数字降级 0.0）
- 验证：正常/前导说明/代码块均能提取；纯垃圾 NO_MATCH 降级

### 大图渲染截断
- GraphView 加 applyGraphTruncation：实体图节点 >300 时按 degree 取 Top 核心节点 + 对应边
- 顶部橙色提示条「节点过多已按关联度显示 Top 300 核心节点」
- 当前 502 节点/4266 边，d3 forceSimulation 全量 SVG 渲染会卡，截断后流畅

### 教训
- 本地 CPU 向量化大 batch 是「一次性全塞 + 无进度」的经典反模式：既阻塞又无反馈，让用户误以为卡死；分批 + 进度回调双向解决
- LLM 结构化输出（JSON 打分）必须防前导文字/代码块包裹 + 非数字元素，纯 json.loads 太脆弱
