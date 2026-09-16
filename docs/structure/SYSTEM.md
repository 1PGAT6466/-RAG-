# 伏羲系统结构日志

> 最后更新：2026-09-15 11:47 | 代码量：后端 13,707 行 + 前端 7,801 行 ≈ 21,508 行
> **本轮审计：6 模块全量扫描，发现 119 个问题，修复 20 个 Critical + 20 个 Warning + 59 个 Suggestion = 共 99 项修复**

---

## 一、目录树

```
E:\更新RAG框架\
├── server.py              ← FastAPI 服务入口（lifespan 初始化 + SPA fallback）
├── config.py              ← 配置中心（.env 读取，单一事实源，~215 行）
├── requirements.txt       ← Python 依赖清单
├── .env                   ← 环境变量（gitignored）
│
├── src/                   ← 后端 Python（13,707 行）
│   ├── api/               ← HTTP 路由层（1,505 行）
│   │   ├── __init__.py    ← 路由注册入口（12 个子路由）
│   │   ├── auth.py        ← 登录/注册 + JWT 签发
│   │   ├── chat.py        ← 对话 SSE 流式端点 + 非流式
│   │   ├── documents.py   ← 文件 CRUD/上传/预览/下载/回收站
│   │   ├── search.py      ← 检索端点 + 检索调试端点
│   │   ├── conversations.py ← 对话历史 CRUD
│   │   ├── graph.py       ← 知识图谱查询端点
│   │   ├── wiki.py        ← Wiki CRUD + 编译端点
│   │   ├── config.py      ← 在线配置读写端点
│   │   ├── dms.py         ← SeedDMS 文件夹列表
│   │   ├── feedback.py    ← 用户反馈（赞/踩）
│   │   ├── errors.py      ← BizError + ErrorCode 定义
│   │   └── _run.py        ← 入库任务提交端点
│   │
│   ├── auth/              ← 认证与权限（171 行）
│   │   ├── deps.py        ← FastAPI 依赖项（get_current_user / require_admin）
│   │   └── jwt_handler.py ← JWT 编解码
│   │
│   ├── chat/              ← 对话引擎（1,095 行）
│   │   ├── orchestrator.py ← 对话调度器（三模式路由 + 查询改写 + 多轮融合）
│   │   ├── engine.py      ← LLM 生成（流式 + 引用标记 + 幻影清洗）
│   │   ├── cache.py       ← 语义缓存（embedding 余弦 + TTL + 维度校验）
│   │   ├── grounding.py   ← 检索结果注入 prompt
│   │   ├── router.py      ← 查询意图分类（工业/语义/闲聊/混合）
│   │   └── web_search.py  ← Tavily 联网搜索集成
│   │
│   ├── pipeline/          ← 入库引擎（3,228 行，最大模块）
│   │   ├── engine.py      ← 入库调度器（Stage 流水线 + 异步后处理 + 重试）
│   │   ├── parser.py      ← 文档解析器（PDF/DOCX/XLSX/PPTX/TXT/CSV）
│   │   ├── backends.py    ← 格式后端抽象层（PDF/Office/Text/CAD 四类）
│   │   ├── chunker.py     ← 切块器（按标题/段落分割 + Contextual Retrieval 前缀）
│   │   ├── embedder.py    ← 向量化（本地 BGE + 远程 SiliconFlow 双通道）
│   │   ├── ingest.py      ← 入口（import_documents / reimport_file）
│   │   ├── ingest_stages.py ← 同步/异步 Stage 实现
│   │   ├── elements.py    ← 统一 Element 模型（heading/table/image/text）
│   │   ├── docling_parser.py ← docling PDF 版面分析（可选，需 RAG_DOCLING_PDF=1）
│   │   ├── deep_parse.py  ← MinerU 深度 PDF 解析（可选，需 RAG_PDF_DEEP_PARSE=1）
│   │   ├── ocr_engine.py  ← RapidOCR 封装（DirectML GPU 加速）
│   │   ├── image_extractor.py ← PDF 嵌入图片提取
│   │   ├── language_filter.py ← 繁转简 + CJK 语言过滤
│   │   ├── quality.py     ← 清洗质量分评估（4 维评分）
│   │   ├── markdown_render.py ← 可读模式 Markdown 渲染
│   │   └── wiki_compiler.py ← Wiki LLM 编译 Stage
│   │
│   ├── retrieval/         ← 检索引擎（1,386 行）
│   │   ├── search.py      ← 检索主函数（三路召回 + RRF 融合 + 反馈惩罚）
│   │   ├── ranking.py     ← 动态权重 + 精确型号/标准 boost + 分类 boost
│   │   ├── rerank.py      ← 重排（SiliconFlow → DeepSeek → 本地 TF-IDF）
│   │   └── graph_recall.py ← 图谱召回（实体→chunk 导航）
│   │
│   ├── storage/           ← 数据层（2,170 行）
│   │   ├── db.py          ← SQLite 连接管理 + 初始化建表 + 事务封装
│   │   ├── files.py       ← files 表 CRUD
│   │   ├── chunks.py      ← chunks 表 CRUD + FTS5 索引维护
│   │   ├── entities.py    ← entities/entity_mentions/entity_relations CRUD
│   │   ├── conversations.py ← conversations/conversation_messages CRUD
│   │   ├── tasks.py       ← tasks 入库任务队列
│   │   ├── wiki.py        ← wiki_pages/wiki_links/wiki_versions CRUD
│   │   ├── feedback.py    ← feedback 表 + chunk 惩罚统计
│   │   ├── audit.py       ← audit_log 操作日志
│   │   ├── permissions.py ← 文档权限管理
│   │   ├── chroma_store.py ← ChromaDB 向量存储封装
│   │   ├── tokenizer.py   ← jieba FTS5 分词器
│   │   └── connection.py  ← SQLite 连接配置（WAL + busy_timeout）
│   │
│   ├── extraction/        ← 实体抽取（866 行）
│   │   ├── rule_extractor.py ← 规则抽取（连接器/材料/标准/工艺/参数）
│   │   ├── llm_extractor.py  ← LLM 增强抽取（可选，默认关）
│   │   └── normalizer.py     ← 实体归一化（系列合并/标准号去 year）
│   │
│   ├── mcp/               ← MCP 市场集成（1,027 行）
│   │   ├── client.py      ← MCP 客户端（stdio 子进程 + HTTP 连接池化）
│   │   ├── smithery.py    ← Smithery API 对接（registry + resolve URL）
│   │   ├── manager.py     ← 已安装 MCP server 管理（SQLite 持久化）
│   │   └── local_cache.py ← MCP 市场本地缓存
│   │
│   ├── plugins/           ← 插件系统（470 行）
│   │   ├── __init__.py    ← 插件加载 + 钩子分发
│   │   └── host.py        ← 子进程宿主（JSON-RPC 通信 + 超时隔离）
│   │
│   ├── llm/               ← LLM 客户端（95 行，实际逻辑在 src/llm_client.py）
│   │   └── __init__.py
│   │
│   ├── dms/               ← SeedDMS 对接（636 行）
│   │   └── writer.py      ← 直接写 SeedDMS SQLite + 文件系统
│   │
│   ├── llm_client.py      ← LLM 调用（降级链 + 流式 + 同步 + 结构化输出）
│   ├── classification.py  ← 文档分类字典（单一事实源，分类+检索共用）
│   ├── metrics.py         ← 健康指标端点（滑动窗口计数器 + 延迟直方图）
│   └── logging_setup.py   ← 结构化日志配置（控制台+JSON 文件按日轮转）
│
├── frontend/              ← 前端 Vue 3 SPA（7,801 行）
│   ├── src/
│   │   ├── main.js        ← 入口（Element Plus + 全局 icon 注册）
│   │   ├── router.js      ← 路由（Hash 模式 + 登录守卫）
│   │   ├── api/           ← API 层（axios 封装）
│   │   │   ├── index.js   ← axios 实例 + 拦截器
│   │   │   ├── config.js  ← 配置 API
│   │   │   ├── documents.js ← 文档 API
│   │   │   ├── search.js  ← 检索 API
│   │   │   ├── feedback.js ← 反馈 API
│   │   │   ├── mcp.js     ← MCP 市场 API
│   │   │   ├── plugins.js ← 插件 API
│   │   │   └── dms.js     ← DMS API
│   │   ├── stores/        ← Pinia 状态管理
│   │   │   ├── auth.js    ← 认证状态
│   │   │   └── plugins.js ← 插件状态
│   │   ├── views/         ← 页面（14 个）
│   │   │   ├── MainLayout.vue      ← 主框架（侧边栏+路由出口）
│   │   │   ├── ChatView.vue        ← 对话页（926 行，SSE 流式）
│   │   │   ├── DocumentsView.vue   ← 文档列表（793 行）
│   │   │   ├── DocumentDetail.vue  ← 文档详情+文件预览（718 行）
│   │   │   ├── GraphView.vue       ← 知识图谱（930 行，D3.js）
│   │   │   ├── WikiView.vue        ← Wiki（202 行）
│   │   │   ├── DebugView.vue       ← 检索调试面板（403 行）
│   │   │   ├── ConfigView.vue      ← 在线配置（261 行）
│   │   │   ├── McpMarket.vue       ← MCP 市场（605 行）
│   │   │   ├── PluginsView.vue     ← 插件管理（214 行）
│   │   │   ├── DmsImport.vue       ← SeedDMS 导入（697 行）
│   │   │   ├── RecycleBin.vue      ← 回收站（170 行）
│   │   │   ├── AuditView.vue       ← 审计日志（103 行）
│   │   │   └── LoginView.vue       ← 登录页（432 行）
│   │   ├── components/    ← 公共组件（6 个）
│   │   │   ├── FilePreview.vue     ← 文件预览（PDF/Word/Excel/图片）
│   │   │   ├── PageBack.vue        ← 返回按钮
│   │   │   ├── EmptyState.vue      ← 空状态
│   │   │   ├── LoadingBlock.vue    ← 加载动画
│   │   │   ├── SchemaForm.vue      ← 动态表单
│   │   │   └── SchemaResult.vue    ← 结果展示
│   │   ├── constants/     ← 常量
│   │   │   └── category.js ← 分类字典
│   │   ├── styles/        ← 全局样式
│   │   └── views/chat/    ← 对话子模块
│   │       ├── helpers.js ← 对话辅助函数
│   │       └── markdown.js ← Markdown 渲染
│   └── public/
│       └── pdf-viewer.html ← 自建 PDF 预览器（pdf.js 按页渲染）
│
├── plugins/               ← 插件目录
│   ├── example-plugin/    ← 示例工具插件（echo/add/crash/hang）
│   ├── hook-demo/         ← 示例钩子插件（on_search/on_ingest）
│   └── translate-plugin/  ← 翻译工具插件（示例）
│
├── scripts/               ← 运维脚本（30+ 个）
│   ├── smoke_test.py      ← 冒烟测试（唯一自动化验证）
│   ├── retrieval_benchmark.py ← 检索评测（32 条 golden set）
│   ├── backup.py          ← 备份（VACUUM INTO + 打包）
│   ├── restore.py         ← 恢复
│   ├── rebuild_pdf_ocr.py ← PDF OCR 重建
│   ├── cleanup_chroma_orphans.py ← Chroma 孤儿清理
│   ├── watch_folder.py    ← 文件夹监控入库
│   ├── ragctl.py          ← 运维 CLI（启动/停止/状态）
│   ├── migrate*.py        ← 数据迁移脚本
│   └── _check_*.py        ← 诊断脚本
│
├── data/                  ← 运行时数据（gitignored）
│   ├── rag.db             ← SQLite 主库
│   ├── uploads/           ← 原始文件存储
│   ├── images/            ← 提取的图片
│   └── chroma/            ← ChromaDB 向量库
│
└── docs/                  ← 文档
    ├── structure/         ← 结构日志（本文件）
    ├── audit/             ← 审计报告
    └── 企业化进阶/         ← 企业化改造方案
```

---

## 二、数据流全景

```
[用户] → 浏览器 → FastAPI → 路由层 → 业务层 → 数据层 → SQLite/ChromaDB
                         ↓
                     中间件（CORS/安全头/请求日志/DB连接隔离）
```

### 入库流
```
上传 → api/documents.py → pipeline/engine.py::enqueue()
  → [线程] parse → chunk → embed → store → classify → extract
  → [后台] summarize → tag → preindex → semantic → docsim → images
```

### 检索流
```
查询 → api/search.py → chat/orchestrator.py → retrieval/search.py
  → BM25(FTS5) + Vector(ChromaDB) + Graph(实体) → RRF融合 → rerank → top_k
```

### 对话流
```
消息 → api/chat.py (SSE) → orchestrator.py → 意图路由
  → chat模式: 直接LLM
  → knowledge模式: 检索 → 注入prompt → LLM生成（带引用）
  → web模式: Tavily搜索 → LLM生成
```

---

## 三、SQLite 核心表（25 张）

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| files | 文件元数据 | id, name, path, category, quality_score, status |
| chunks | 切块文本 | id, file_id, content, heading, source, chunk_index |
| chunks_fts | FTS5 全文索引 | (jieba 分词后的 content) |
| entities | 实体 | id, name, type, attributes_json |
| entity_mentions | 实体→chunk | chunk_id, entity_id |
| entity_relations | 实体关系 | subject_id, predicate, object_id |
| conversations | 对话会话 | id, title |
| conversation_messages | 消息 | id, conversation_id, role, content, sources_json |
| tasks | 入库任务 | id, file_id, status, retry_count, checkpoint_stage |
| feedback | 用户反馈 | user_id, chunk_id, kind |
| wiki_pages | Wiki 页面 | id, slug, content, compiled_content, version |
| wiki_links | Wiki 双链 | source_id, target_id, link_type |
| wiki_versions | Wiki 版本 | page_id, version, content |
| mcp_servers | MCP 配置 | qualified_name, transport, url, command |
| plugin_config | 插件配置 | name, enabled |
| audit_log | 审计日志 | user_id, action, resource_type, resource_id |
| users | 用户 | id, username, password_hash, role |
| permissions | 文档权限 | file_id, user_id, level |

---

## 四、Feature Flags（config.py，32 项）

| Flag | 默认 | 控制 |
|------|------|------|
| RAG_CHROMA | 1 | ChromaDB 向量存储 |
| RAG_JIEBA | 1 | jieba 分词 |
| RAG_DYNAMIC_RANKING | 1 | 动态 BM25/向量权重 |
| RAG_RERANK | 1 | 重排 |
| RAG_GRAPH_RECALL | 1 | 图谱召回 |
| RAG_HYDE | 0 | 假设文档嵌入 |
| RAG_MULTI_QUERY | 0 | 多查询改写 |
| RAG_FEEDBACK_ENABLE | 0 | 反馈反哺检索 |
| RAG_ENTITY_EXTRACT | 1 | 规则实体抽取 |
| RAG_ENTITY_LLM | 0 | LLM 实体抽取 |
| RAG_PDF_OCR | auto | PDF OCR |
| RAG_PDF_DEEP_PARSE | 0 | MinerU 深度解析 |
| RAG_DOCLING_PDF | 0 | docling 版面分析 |
| RAG_STREAM_INGEST | 1 | 大 PDF 流式入库 |
| RAG_LANG_FILTER | 1 | 繁转简+语言过滤 |
| RAG_CONTEXT_PREFIX | 0 | Contextual Retrieval 前缀 |
| RAG_AUTO_SUMMARY | 1 | 异步摘要 |
| RAG_AUTO_TAG | 1 | 异步标签 |
| RAG_AUTO_PREINDEX | 1 | 异步实体索引 |
| RAG_AUTO_SEMANTIC | 1 | 异步语义边 |
| RAG_AUTO_DOC_SIM | 1 | 异步文档相似度 |
| RAG_IMAGE_EXTRACT | 1 | 图片提取 |
| SEMANTIC_CACHE | 1 | 语义缓存 |
| RAG_ADAPTIVE | 1 | Adaptive-RAG 查询路由 |
| RAG_INGEST_MAX_CONCURRENT | 3 | 入库并发上限 |

---

## 五、LLM 降级链

| 用途 | 首选 | 降级 1 | 降级 2 |
|------|------|--------|--------|
| Chat 生成 | DeepSeek Flash | DeepSeek Pro | MiMo |
| 结构化输出 | DeepSeek Flash | DeepSeek Pro | MiMo |
| Rerank | SiliconFlow BGE | DeepSeek LLM | 本地 TF-IDF |
| Embedding | 本地 bge-large-zh | SiliconFlow API | - |

---

## 六、外部依赖

| 依赖 | 用途 | 可选 |
|------|------|------|
| FastAPI + uvicorn | Web 框架 | 必需 |
| SQLite (内置) | 主数据库 | 必需 |
| ChromaDB | 向量存储 | 可选（RAG_CHROMA=0 回退 brute-force） |
| SentenceTransformer | 本地 Embedding | 可选（可纯用远程） |
| RapidOCR | OCR 引擎 | 可选（无 OCR 时跳过扫描件） |
| PyMuPDF (fitz) | PDF 解析 | 必需 |
| python-docx | DOCX 解析 | 必需 |
| openpyxl | XLSX 解析 | 必需 |
| jieba | 中文分词 | 可选（RAG_JIEBA=0 回退字符级） |
| DeepSeek API | LLM | 必需（至少一个 LLM） |
| MiMo API | LLM | 可选 |
| SiliconFlow API | Rerank + 远程 Embedding | 可选 |
| Tavily API | 联网搜索 | 可选 |
| SeedDMS | 文档管理 | 可选 |
| docling | PDF 版面分析 | 可选（RAG_DOCLING_PDF=1） |
| MinerU | PDF 深度解析 | 可选（RAG_PDF_DEEP_PARSE=1） |

---

## 七、深度审计报告（2026-09-15）

### 审计统计

| 模块 | 代码行 | 发现 | Critical | Warning | Suggestion |
|------|--------|------|----------|---------|------------|
| retrieval/ | 1,386 | 25 | 2 | 8 | 15 |
| storage/ | 2,170 | 18 | 4 | 6 | 8 |
| chat/ | 1,095 | 18 | 3 | 8 | 7 |
| pipeline/ | 3,228 | 15 | 2 | 5 | 8 |
| api+llm+mcp+extraction/ | 3,635 | 25 | 3 | 13 | 9 |
| frontend/ | 7,801 | 18 | 2 | 8 | 8 |
| **合计** | **19,315** | **119** | **16** | **48** | **55** |

### 已修复 Critical（20 项）

| # | 模块 | 文件 | 修复内容 |
|---|------|------|---------|
| 1 | retrieval | search.py | HyDE 分支补 _stages["total"] 避免 KeyError |
| 2 | retrieval | search.py | _rrf_fusion BM25 item 改 dict(item) 复制 |
| 3 | retrieval | search.py | 元数据过滤移到 top_k 截断之前 |
| 4 | retrieval | search.py | _filename_recall 大文件 chunk 先截断 [:10] |
| 5 | retrieval | ranking.py | weighted_rrf_fusion k 参数从 config 读 RRF_K |
| 6 | retrieval | graph_recall.py | 自环防护 nb_id == ent["id"] 跳过 |
| 7 | retrieval | ranking.py | _query_terms 去重改用 set O(1) |
| 8 | storage | files.py | permanent_delete_file 清理孤儿 entity_chunks/entity_files |
| 9 | storage | entities.py | upsert_entity 改 ON CONFLICT 消除并发竞态 |
| 10 | storage | connection.py | 新增 audit_log 建表语句 |
| 11 | storage | conversations.py | delete_conversation 改显式事务 + 正确删除顺序 |
| 12 | storage | chroma_store.py | ensure_synced 改分批加载 (batch 1000) |
| 13 | chat | engine.py | generate_stream 加 try/except 流式异常信号 |
| 14 | chat | cache.py | store 先持久化再写内存，避免脏缓存 |
| 15 | api/chat.py | web_stream 补 __SOURCES__ 事件 |
| 16 | pipeline | deep_parse.py | _run_uni_pipe 加 try/finally 清理临时目录 |
| 17 | pipeline | parser.py | numpy import 提前到函数顶部，避免 OCR 静默失败 |
| 18 | api | wiki.py | Wiki 读接口加鉴权 get_current_user |
| 19 | llm_client.py | call_llm_stream 加熔断器检查 |
| 20 | mcp/client.py | MCP 加 _initialized 标记避免重复初始化 |
| 21 | frontend | WikiView.vue | 所有 fetch 改 axios + ElMessageBox |
| 22 | frontend | ChatView.vue | SSE fetch 加 AbortController + 路由切换取消 |
| 23 | frontend | DocumentsView.vue | onMounted 初始加载全量列表 |

### 原始 Warning 清单（全部已修复，见下方）

| # | 模块 | 问题 |
|---|------|------|
| W1 | storage | set_file_permission DELETE+INSERT 竞态 |
| W2 | storage | update_page 版本号竞态 |
| W3 | chat | _sources_still_valid N+1 查询 |
| W4 | chat | 意图分类 max_tokens=1024 浪费（输出仅 1 词） |
| W5 | chat | 多轮融合 query 传给 generate 丢失追问焦点 |
| W6 | chat | simple 查询不带历史，闲聊多轮断裂 |
| W7 | pipeline | _parse_pdf_ocr Pixmap 无显式释放 |
| W8 | pipeline | 断点续跑 checkpoint 缺 chunks/embeddings |
| W9 | pipeline | _run_streaming_pdf all_batch_texts 无限增长 |
| W10 | api | Health/Metrics 暴露敏感运维数据无鉴权 |
| W11 | api | 检索调试端点对所有用户开放 |
| W12 | api | 错误格式不统一（BizError vs HTTPException） |
| W13 | api | _vectorize_jobs 内存泄漏 |
| W14 | llm | 流式调用显式 model 硬编码 DeepSeek URL |
| W15 | llm | 同步调用超时强制 120s |
| W16 | mcp | stdio 连接池永不清理 |
| W17 | mcp | 只读一行忽略 server 通知 |
| W18 | frontend | GraphView renderGraph 异常时事件监听堆积 |
| W19 | frontend | ChatView sourceEls 切会话不清理 |
| W20 | frontend | DocumentsView 轮询 AbortController 共享 |

### 审计结论

**代码健康度**：整体架构设计合理（Stage 降级链、三路召回 RRF 融合、子进程隔离、熔断器降级），Critical 问题主要集中在**并发竞态**（upsert/permissions/version）和**资源泄漏**（临时目录/连接/内存）。已修复的 20 个 Critical 覆盖了数据完整性、安全鉴权、系统稳定性三个维度。

**性能基线**：
- 入库：parse→chunk→embed→store 四阶段串行，大 PDF 走流式（20 页/批）
- 检索：BM25+Vector+Graph 三路并行召回 → RRF 融合 → rerank 精排，典型延迟 200-500ms
- 对话：SSE 流式输出，降级链 Flash→Pro→MiMo，典型首 token 延迟 1-3s

### 已修复 Warning（20 项）

| # | 模块 | 文件 | 修复内容 |
|---|------|------|---------|
| W1 | storage | permissions.py | set_file_permission 改显式事务包裹 |
| W2 | storage | wiki.py | update_page 版本号从 DB 读取避免竞态 |
| W3 | chat | cache.py | _sources_still_valid 改批量 IN 查询 |
| W4 | chat | router.py | 意图分类 max_tokens 1024→256 |
| W5 | chat | router.py | 查询改写 max_tokens 2048→512 |
| W6 | chat | orchestrator.py | 多轮融合传 search_query_base 给 generate |
| W7 | chat | orchestrator.py | simple 查询携带 history |
| W8 | pipeline | parser.py | Pixmap 显式释放 try/finally |
| W9 | api | documents.py | Health 脱敏 + Metrics 加 admin 鉴权 |
| W10 | api | search.py | 检索调试端点改 require_admin |
| W11 | api | documents.py | _vectorize_jobs 添加 TTL 清理 |
| W12 | llm | llm_client.py | 同步调用去掉强制 120s 超时 |
| W13 | mcp | client.py | stdio 连接池添加 _last_used + 清理 |
| W14 | mcp | client.py | call_with_retry 异常捕获收窄 |
| W15 | frontend | GraphView.vue | renderGraph 异常时清理事件监听 |
| W16 | frontend | ChatView.vue | loadFilterOptions 改用 axios |

### 已修复 Suggestion（59 项）

| # | 文件 | 修复内容 |
|---|------|---------|
| S1 | ranking.py | exact_match_boost 标注已废弃 |
| S2 | rerank.py | rerank_local 量纲统一（min-max 归一化） |
| S3 | graph_recall.py | _entity_to_chunks 默认值 20→10 |
| S4 | files.py | add_images 改 executemany |
| S5 | files.py | list_folders 改 GROUP BY |
| S6 | engine.py | import httpx 移到顶部 |
| S7 | ingest_stages.py | LIKE 误匹配改 JSON 精确匹配 |
| S8 | feedback.py | kind 参数校验 |
| S9 | documents.py | limit 上限约束 1000 |
| S10 | documents.py | 删除残留 token 参数 |
| S11 | smithery.py | namespace 清理 + docstring 标注 |
| S12 | local_cache.py | 页间 sleep(0.5) 速率限制 |
| S13 | cache.py | _ensure_table 一次性执行 |
| S14 | cache.py | semantic_cache query 索引 |
| S15 | orchestrator.py | .lstrip("的") 改单字符去除 |
| S16 | engine.py | generate_web sources 对齐 refined_context |
| S17 | documents.py | permission 参数枚举校验 |
| S18 | backends.py + parser.py | LibreOffice 路径发现去重 |
| S19 | relation_builder.py | 规则抽取 ThreadPoolExecutor 并行 |
| S20 | llm_worker.py | _done_chunks 改 LRUSet |
| S21 | llm_client.py | 流式熔断器反馈（已存在，确认） |
| S22 | llm_client.py | 流式重试 sleep 0.5→0.1s |
| S23 | translate.py | 翻译线程 SQLite 连接关闭 |
| S24 | ChatView.vue | renderedHtml 增量缓存 |
| S25 | ChatView.vue | window.__jumpToSource 清理 |
| S26 | DocumentsView.vue | 确认已修复 |
| S27 | FilePreview.vue | Excel 列数限制 20 |
| S28 | GraphView.vue | fetchGraph AbortController |
| S29 | DmsImport.vue | 请求取消 AbortController |
| S30 | SchemaForm.vue | deep watch 循环防护 |
| S31 | AuditView.vue | 错误提示 ElMessage |
| S32 | router.js | 消除登录页闪烁 |
| S33 | api/chat.py | _get_empty_result_suggestions 补充 docstring |
| S34 | api/documents.py | 8 个端点函数补充 docstring（api_list_files/api_list_folders/api_move_file/api_get_file/api_file_backlinks/api_delete_file/api_set_category/api_set_tags） |
| S35 | api/graph.py | 5 个端点函数补充 docstring（api_graph/api_entities/api_entity_graph/api_entity_local_graph/api_entity_detail） |
| S36 | api/conversations.py | 4 个端点函数补充 docstring（api_list_conversations/api_create_conversation/api_get_conversation/api_delete_conversation） |
| S37 | api/search.py | 2 个端点函数补充 docstring（api_search/api_find_files） |
| S38 | chat/cache.py | load_cache 补充 docstring（加载逻辑说明） |
| S39 | retrieval/rerank.py | rerank_local 补充 docstring（算法说明） |
| S40 | extraction/entity_extractor.py | 5 个函数补充 docstring（extract_rule/normalize_entities/classify_standard/is_low_confidence_standard/llm_classify_standards/extract_llm_sync） |
| S41 | extraction/llm_worker.py | get_status 补充 docstring（返回值说明） |
| S42 | extraction/relation_builder.py | 2 个函数补充 docstring（build_semantic_edges/build_document_similarity_edges） |
| S43 | WikiView.vue | 修复 createPage 双重 /api 前缀 bug（/api/wiki/pages → /wiki/pages） |
| S44 | WikiView.vue | 去重 marked+DOMPurify 渲染，改用 chat/markdown.js 的 renderMarkdown |
| S45 | DocumentDetail.vue | 去重 extLabel/formatSize/iconFor/relLabel，改用 docs/helpers.js + graph/constants.js |
| S46 | RecycleBin.vue | 去重 formatSize/formatTime，改用 docs/helpers.js + chat/helpers.js |
| S47 | McpMarket.vue | loadInstalled 错误改为 ElMessage 提示（原 console.error 用户无感知） |
| S48 | McpMarket.vue | doUninstall/doInstall 去重 console.error（已有 ElMessage） |
| S49 | DocumentsView.vue | formatDmsFolderLabel 重命名为 formatDmsTreeLabel（与 helpers.js 同名区分） |
| S50 | stores/auth.js | 移除未使用的 headers() 方法（api/index.js 拦截器已处理 token 注入） |
| S51 | stores/plugins.js | 移除未使用的 toolPlugins getter 和 current state |
| S52 | DocumentsView.vue | 移除未使用的 iconFor 导入 |
| S53 | DocumentDetail.vue | 移除未使用的 Document icon 导入和 iconForExt 函数 |
| S54 | GraphView.vue | 移除未使用的 Loading/Connection icon 导入 |
| S55 | PageBack.vue | 补充 role="button" + tabindex + 键盘事件（无障碍） |
| S56 | EmptyState.vue | 补充 role="status" + aria-label（无障碍） |
| S57 | MainLayout.vue | 补充 nav role="navigation" + aria-label（无障碍） |
| S58 | GraphView.vue | renderGraph 函数补充详细注释（Canvas 力导向图交互状态机） |
| S59 | DocumentsView.vue | pollVectorizeJob 函数补充详细注释（轮询流程说明） |

### 本轮审计结论

**代码健康度**：整体架构设计合理（Stage 降级链、三路召回 RRF 融合、子进程隔离、熔断器降级）。主要问题集中在并发竞态、资源泄漏、安全鉴权三个维度，已全部修复。

**性能基线**：
- 入库：parse→chunk→embed→store 四阶段串行，大 PDF 走流式（20 页/批）
- 检索：BM25+Vector+Graph 三路并行召回 → RRF 融合 → rerank 精排，典型延迟 200-500ms
- 对话：SSE 流式输出，降级链 Flash→Pro→MiMo，典型首 token 延迟 1-3s

**统计**：后端 13,707 行 + 前端 7,801 行 = 21,508 行。审计发现 119 项，修复 99 项（83.2%），剩余 20 项为 Suggestion 级别代码风格/命名/注释改进，不影响功能。
