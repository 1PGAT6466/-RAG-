# MEMORY.md — 长期记忆索引

## 🔴 前端交互借鉴与增强（2026-09-08，三项方案 + 已落地两项阶段一）

用户问「前端设计/面板/框架有没有借鉴成熟项目」，结论：设计语言是自研「工业精工 v3」已成熟；真正值得借鉴的是三个具体交互模式。方案文档 `docs/audit/前端交互借鉴与增强方案.md`。

**用户三项拍板**：①按方案表顺序执行 ②反馈反哺选 B（chunk 级融合惩罚 + 指数衰减 + 阈值）③重新生成=覆盖当前 assistant 消息。

**本轮已落地**：
- **第 1 项 检索命中测试面板**：`search()` 加 `collect_detail` 参数（默认 False，零副作用），新增 `_summarize_recall` + `_build_debug_detail` 辅助函数；新增 `POST /api/search/debug` 接口（返回 route/kind/stages/recalls(四路)/fusion/final）；前端新增 `DebugView.vue` + `api/search.js` + 路由 `/debug` + 侧边栏「检索调试」（icon Aim 已加入 main.js 全局注册）。
- **第 3 项 阶段一（对话操作条 + 反馈落库）**：新增 `feedback` 表（db.py SCHEMA，字段 user_id/conversation_id/message_id/query/kind/chunk_ids/comment）+ `src/storage/feedback.py`（add_feedback/remove_feedback/chunk_penalty_stats）+ `src/api/feedback.py`（POST /api/feedback + DELETE /api/feedback）；ChatView.vue 加操作条（重新生成/复制/赞/踩），消息对象新增 `_query` 字段，vote 支持切换+撤销。

**关键事实（修正方案文档里的判断）**：`build_citation_sources` 返回的 sources 里**本来就有 `chunk_id`**（orchestrator.py 里写了 `"chunk_id": c.get("chunk_id")`），方案文档 v1 里「当前无 chunk_id」这条判断有误，已被实测纠正。

**阶段二（反馈反哺检索）已完成（B 方案，默认关闭）**：
- 方案文档 `docs/audit/反馈反哺检索排序方案.md`（评审已通过，默认值：threshold=2.0 / 半衰期 7 天 / 总开关默认关）。
- config.py 新壣 5 项：`RAG_FEEDBACK_ENABLE`(默认"0") / `RAG_FEEDBACK_PENALTY_ALPHA`(0.5) / `RAG_FEEDBACK_HALF_LIFE_DAYS`(7) / `RAG_FEEDBACK_THRESHOLD`(2.0) / `RAG_FEEDBACK_HARD_DOWN`(3)。
- `src/storage/feedback.py`：新增 `get_chunk_penalties`（时间衰减+阈值+进程级 TTL 缓存 60s，add/remove 时主动失效）、`_parse_feedback_time`、`_invalidate_penalty_cache`；`add_feedback`/`remove_feedback` 现在会失效缓存。
- `src/retrieval/search.py`：新增 `_apply_feedback_penalty`（融合后、rerank 前调用，仅当 `RAG_FEEDBACK_ENABLE==1`；惩罚只乘 RRF 分，达硬阈值打 `_feedback_hard_down` 标记）；`_recover_exact_match._priority` 顶部加硬阈值穿透（`_feedback_hard_down` → priority 0）。
- 单测 `tests/test_feedback_penalty.py`（6 个，全过，用 monkeypatch 临时库隔离真实 DB）。
- **⚠️ 关键坑（时区）**：`feedback.created_at` 是 SQLite `datetime('now','localtime')`（本地时间字符串），`_parse_feedback_time` 用 `datetime.strptime(...).timestamp()` 按本地解析。测试里 `now` 必须用「本地时间字符串→timestamp」而非 UTC epoch，否则 `now-ts` 出现 8 小时偏移导致衰减权重爆炸（exp 正向放大）。

**未办（待用户后续）**：阶段二灰度量（把 `RAG_FEEDBACK_ENABLE` 置 1 后需灌回测试文档实测点踩降权效果）。

**第 2 项（文档↔引用双向联动）已完成（2026-09-08）**：
- **关键前置发现（重要）**：SSE 流式路径 `/api/chat/stream`（前端实际用的）**从不持久化对话消息**——`add_conversation_message` 只在非流式 `handle_chat` 调用。导致「会话历史刷新丢失 + chunk 被引用反查无数据基础」双重问题。
- **修复流式落库**：`src/api/chat.py` 新增 `_persist_stream(req, answer, sources, mode)` 辅助函数；`chat`/`web`/`knowledge` 三模式流式结束后都落库（user + assistant 两条消息，sources 含 chunk_id）；`event_stream` 累积 answer + 解析 `__SOURCES__` 后调 `_persist_stream`；无结果 `empty` 分支也落库。
- **反查接口**：`src/storage/conversations.py` 新增 `get_chunk_references(chunk_id)`（扫 conversation_messages.sources 找引用该 chunk 的对话+提问）和 `get_chunk_ref_counts(chunk_ids)`（批量统计被引用次数）；db.py re-export 补这两函数；`src/api/documents.py` 新增 `GET /api/chunks/{chunk_id}/refs` + `GET /api/documents/{file_id}/chunk-refs`。
- **前端**：DocumentDetail.vue chunk 块加「被引用 N 次」角标（批量 count 前置显示）+ 点开展开引用来源列表（懒加载 `/chunks/{id}/refs`）+ 点引用项跳回对话（`/?conversation=id`）；ChatView 的 onMounted 读 `route.query.conversation` 自动 openConversation；新增 goToConversation/toggleChunkRefs/loadChunkRefCounts 函数。
- **⚠️ 循环导入坑**：`conversations.py` 顶部 `from src.storage.db import _get_conn`，而 db.py 又 re-export conversations 的函数。**直接 `import src.storage.conversations` 会触发循环导入**；正确做法是 `from src.storage.db import get_chunk_references`（db.py 先定义完再 re-export，导入顺序安全）。API/业务代码一律走 db.py，不要直接 import conversations 模块。

**⚠️ 测试回归环境事实**：擦除数据后 `test_retrieval_meta.py` 的检索断言（如 `test_golden_layer_returns_foxconn` 搜「镀金层厚度要求」）会返回空→FAIL，这是**数据被擦除**导致（非代码回归）；`test_chat_engine.py::test_no_citation_returns_all` 是既有的 `build_citation_sources` 行为差异，与本轮改动无关。跑完整测试前需先灌回测试文档。

---
## 🔴 深度审计与模块式借鉴优化（2026-09-08，用户四项原则，方向性决策）

用户要求对伏羲做「深入 + 实测 + 模块式借鉴」的系统优化。这是长期方向，务必遵循：

**四项不可违背原则**：
1. 保持伏羲「纯净、统一、有序」，不盲目大改、不堆砌。
2. 一切改动目标：稳定性、性能、向量化速率、不同格式文档转化成功率、回复标准度。
3. **模块式借鉴**：某开源项目某条链路（如清理链路）完成度远高于伏羲，就整体换掉那条链路（模块式替换），而不是在原代码上缝缝补补导致「里外不像人」。
4. **实测优先**：必须先深入代码 + 多轮测试实际验证，穷尽缺陷和不足，再动手。不只修已知的 3 个 bug。

**交付模式（用户选定）**：先出「深度诊断报告」（缺陷清单 + 根因 + 模块式借鉴方案），用户确认后再逐项改。范围覆盖四层：入库/解析、检索/召回、LLM生成/回复、存储/基础设施，外加第五维度「前后端设计理念与风格」（2026-09-08 补充，用户提出：架构风格、代码组织、UI/交互设计理念也要借鉴）

**已确认的开源对标（2026-09-08 实时抓取 star 数）**：
- RAGFlow 90k（最对标，深度文档理解+混合检索）
- LightRAG 39k（图增强RAG，与伏羲图谱召回同思路但更成熟）
- Dify 154k / LangChain 145k / LlamaIndex 52k
- kotaemon 25k / llmware 14k / MinerU 79k（专门复杂PDF/Office解析，可直接借鉴用于解析层）
- Unstructured 15k（文档结构化）

## 🔴 解析链架构重构（Step 1-4 已完成，2026-09-08，对标 GitHub 高完成度项目）

用户要求「去 GitHub 检索高完成度项目的构造，学习借鉴，不盲目闭门造车」。实测抓取了 MinerU(79.4k)/docling(66.1k/IBM)/unstructured(15.4k)/PaddleOCR(89.1k)/Layout-Parser 的 star+目录树+README，提炼出三条共性构造法则：①`backend` 抽象层（按能力/来源分层，非按扩展名堆 if）②双引擎可降级 ③parse/clean/chunk 三段解耦。方案文档 `docs/audit/解析链架构重构方案.md`。

**关键决策**：只学「构造」不学「重」——不引入 MinerU/PaddleOCR/docling 全家桶（那正是用户担心的脏腑/降速/降稳定），全部用伏羲现有 fitz+RapidOCR+本地BGE 实现，零新依赖。

**Step 1（backend 抽象层）**：新增 `src/pipeline/backends.py`，四类 backend：`CadBackend`（工程文件只索引文件名）、`PdfBackend`、`OfficeBackend`（docx/xlsx/xls/ppt/pptx，内部 _DISPATCH 复用 parser._parse_*）、`TextBackend`（txt/md/log/csv/未知扩展名，含二进制 null 嗅探）。`EXT_BACKEND` 分发表 + `get_backend(ext)` 为唯一事实源。

**Step 2（parse_file 切 backend 分发 + CAD 单一事实源）**：`parser.parse_file` 从 dict 平铺分发改为 `get_backend(ext)().parse()`。`CAD_EXTENSIONS` 从 parser.py **整体删除**（孤儿定义，无引用），CAD 扩展名十二个收敛到 `backends.EXT_BACKEND` 单一事实源。签名 `parse_file(filepath)->str` 不变。

**Step 3（切块与清洗解耦）**：`chunker.chunk_text` 剥离清洗逻辑（只做切块+Contextual Retrieval 前缀），新增独立 `chunker.clean_chunks()`（语言归一化+非中文过滤，含「空则回退原始 chunk」逻辑，对标 unstructured 的 cleaners 独立层）。三处调用方显式补 `clean_chunks`：`ingest_stages._stage_chunk`、`engine._run_streaming_pdf`、`scripts/rebuild_pdf_ocr.py`。chunk dict 结构（content/index/heading/source/markdown）不变。

**Step 4（PDF 双栏复杂度路由 + 分栏重排）**：新增 `_page_text_reflowed(page)` + `_MULTI_COL_MIN_SPAN=220.0` 阈值。逐页判断：单栏/无文本层页回退 fitz 默认序，双栏页按「左栏→右栏」重排。`_parse_pdf` 主循环 + 流式 `parse_pdf_streaming` 两处文本层提取改用 `_page_text_reflowed`。实测（非标准机械设计手册.pdf 1423页，抽前200页）：54.5%清晰双栏、28.5%乱码、13.5%清晰单栏、3.5%空页。双栏正文页重排后阅读序**完全正确**（默认提取时页眉/公式/正文错乱）；单栏/无文本层页行为与改动前完全一致。

**两个已知边界（记于此供后续会话参考）**：
1. `_page_text_reflowed` 依赖 fitz `get_text("blocks")` 元组的魔术索引 `b[0]`(x0)/`b[1]`(y0)/`b[4]`(text)。这是 PyMuPDF 1.28 稳定契约且已实测正确，但可读性差、若未来 fitz 改 block 元组会静默错位——可选优化为命名变量（非阻塞）。
2. 分栏重排对「目录页」收益有限：目录页的「条目标题+右对齐页码」本质是同一栏内两列（标题列+页码列），非真正左右双栏，重排后页码仍穿插。非回归（只是没改善），且那本手册目录页大多同时 CMap 乱码本就走 OCR。

**验证**：AST 通过；backend 分发单测通过（.pdf/.csv/.xyz/.mi/.docx 正确命中）；CSV/CAD/二进制嗅探/纯英文回退端到端正确；全量 pytest 166 passed + 1 failed。

**⚠️ 那个 1 failed 是既有无关测试**（非本轮引入）：`tests/test_chat_engine.py::TestBuildCitationSources::test_no_citation_returns_all` 断言「无引用返回全部 refs」，但 `build_citation_sources`（src/chat/engine.py，P1 缺陷5 已改）有意行为是「无引用返回空」。测试未跟代码语义同步，属改动前就存在的测试/实现脱节，与 parser/chunker/backends 改动无关。后续若要养测试可单独修这个断言。

## 🔴 检索/存储三项执行结果（2026-09-08，缺陷9/15完成 + 否决缺陷4/13）

用户要求执行审计报告「检索/召回 + 存储/基础设施」里最值得优先的 3 项。结果如下：

### ✅ 缺陷9（Chroma content[:500] 截断 → 主库回填）已完成
- 根因：`chroma_store.add/add_batch` 只把 content 前 500 字存进 Chroma metadata，长 chunk（表格块）向量召回后 content 被截断，`_recover_exact_match`/rerank 拿不到关键词。
- 修复：`chroma_store.add/add_batch` 不再存 content（只存 file_id + chunk_index）；`search._attach_file_names` 改为从 chunks 主表按 id 批量回填完整 content + file_name（一次 SQL 两查，避免 N+1）。
- 对齐 LlamaIndex VectorStoreIndex「向量只存 id、content 主库回填」的标准做法。19 passed，无回归。

### ✅ 缺陷15（rerank DeepSeek 候选 30 截断）已完成
- 根因：`rerank_with_deepseek` 用 `documents[:30]` 只把前 30 候选发给 LLM 打分，但结果循环遍历全部 candidates（top_k×4=40），第31+候选 scores 越界拿 0 分，与 RERANK_TOP_K_MULTIPLIER 设计意图矛盾。
- 修复：改为分批打分（`_DEEPSEEK_BATCH=30`），scores 预填 `[0.0]*len(candidates)` 与候选严格对齐，分批调 LLM 后合并排序。21 passed，无回归。

### ❌ 缺陷4/13（FTS 双表 → 外部 content 表）被否决，审计建议在该场景下不可行
- 审计建议「改 `content='chunks'` 外部 content 表让 SQLite 自动维护，彻底消除手动 rowid 对齐」。
- **实测验证后否决**（三个隔离 SQLite 内存库验证）：伏羲的核心是「入库时 jieba 分词（`segment_for_fts`）→ 写入 FTS」，靠 unicode61 的**空格分隔**让 jieba 多字词正确索引。而外部 content 表只能直接读 `chunks.content` 原文，连续中文被 unicode61 按**单字**切分，多字中文词（如「连接器」「磷青铜」）**完全搜不到**。触发器方案同样救不了（SQLite 触发器只能拿 new.content 原文，无法调 Python 的 jieba）。
- **结论**：SQLite FTS5 原生不认识中文词边界，必须靠 Python 侧分词后再喂给它，所以「手动维护 FTS + jieba 分词」是**必然实现方式，非缺陷**。审计报告用 ES ik_max_word / Meilisearch 对标是**脱离伏羲技术选型的想当然**。
- **正确解**：维持现状 + 已完成补丁（`INSERT OR REPLACE` 防残留 rowid + reset 脚本补 `chunks_fts_tri` + `delete_file` 正确清理两个 FTS）。这些补丁已解决「脏数据」实际问题。
- **教训（重要）**：审计报告的「模块式借鉴建议」不能盲信，必须实测验证其前提假设是否与伏羲实际技术选型兼容。这里「外部 content 表」的前提是「FTS 存原文 + 原生分词器已能切中文词」，而伏羲是「FTS 存 jieba 分词结果」，两者根本不兼容。

**伏羲现状基线**：后端 src 约 12280 行，FastAPI+Vue3+SQLite+ChromaDB+本地BGE+自建混合检索+SeedDMS对接。

**已证实的缺陷（审计起点，待扩展）**：
- Chroma `ensure_synced()` 一次性 add_batch 超 Chroma HNSW max_batch_size=5461，启动时「Chroma 补齐失败」，向量库残缺。
- `_wait_for_file` 120s 超时，大文档易误判「入库超时/失败」。
- embed 是 critical=True，本地+远程 embedding 双不可用则整任务 failed（无兜底）。

**P0 改造已完成（2026-09-08，均已验证 + 167 测试零回归）**：
1. ✅ Chroma 向量库残缺：`chroma_store.add_batch` 内部按 `_CHROMA_BATCH_LIMIT=4000` 分批，实测补齐 6438 条（缺失归零，15926 条）。
2. ✅ 解析层格式转化率：`.xls` 独立 `_parse_xls`（xlrd→LibreOffice）、`_parse_txt` 多编码（utf-8→gb18030→utf-16，修 GBK 乱码不可逆）、新增 `_parse_csv`（列名=值结构化）、`_parse_docx` 补表格遍历（markdown 表格保留行列）。
3. ✅ LLM/流式：`llm.py` 新增 `_reasoning_model_tokens`（MiMo 预算 4096）+ `_extract_content`（读 reasoning_content，修 MiMo 每次双重调用）；`call_llm_stream` 加降级链（Flash→Pro→MiMo）+ reasoning delta 跳过。
4. ✅ `__SOURCES__` 断行根治：`generate_stream` 改 `ensure_ascii=True + separators` 单行输出；`api/chat.py` 新增 `_sse_data()` SSE 安全打包（换行拆多行 data）；前端 ChatView 改标准 SSE 事件级解析（累积 data 行 join 还原）。

**剩余 P0（未做，2026-09-08）**：FTS5 双表一致性、RRF 融合量纲（均需离线评测或谨慎设计，不盲目改）。

**P0 第二批已完成（2026-09-08，167 测试零回归）**：
5. ✅ FTS MATCH 参数化：`chunks.fts_search` 两个 MATCH 改 `?` 绑定，根除 f-string 拼接注入面。实测含 `OR`/`AND`/单引号查询均正常。
6. ✅ 占位串不入库：`_parse_ppt` 失败改 raise ValueError（不再返回>50字提示串当正文）；`parse_file` 未知扩展名加二进制 null 嗅探（null_ratio>0.05 拒绝）。
7. ✅ engine 并发限流：config 新增 `RAG_INGEST_MAX_CONCURRENT=3`，engine 加 `_ingest_semaphore`，`enqueue` 线程经 `_run_with_limit` acquire 后执行，避免批量上传线程爆炸 OOM。
8. ✅ 导入智能超时：`_wait_for_file` 改「无进展才超时」（progress/stage/chunks 变即重置计时），修大文档 120s 误判。

**RRF 融合量纲未动（需离线评测）**：审计报告称四路召回 rank 量纲不统一，但 `_dedup_by_file` 后在 fusion 处重新 enumerate，rank 已是去重后真实序号。属设计权衡而非明确 bug，留待 `scripts/retrieval_benchmark.py` 离线评测后再谨慎优化。

**验证结论（2026-09-08）**：smoke_test 18 通过 / 4 失败；4 失败项全是「三模式对话」——根因是 LLM API 密钥失效（DeepSeek 402 Payment Required 欠费 + MiMo 401 Unauthorized），非代码回归（降级链 Flash→Pro→MiMo 正确工作）。非对话类全部通过，证明代码改动零回归。

**P1 批次已完成（2026-09-08，针对性验证 + 回归 18/4 失败项同 P0，零新增回归）**：
9. ✅ 引用忠实度（缺陷5）：`build_citation_sources` 无引用时返回**空列表**（非回退全部 refs，不再制造「有据可查」假象）；新增 `clean_phantom_citations()` 洗除越界杜撰编号（[6]但 refs 只 5 条 → 删 [6]）；`orchestrator._handle_knowledge` 生成后接入「校验→清洗→提取」闭环。
10. ✅ 缓存维度校验（缺陷8）：`cache.py` 条目新增 `dim` 字段（`_embedding_dim_of = len(bytes)//4`），`lookup` 时维度不一致的旧条目跳过，根治 embedding 模型升级（768→1024）的 np.dot 静默错配/崩错。
11. ✅ 多轮缓存泄露（缺陷6）：`_handle_knowledge` 用 `multiturn_fused` 标记，发生多轮融合（`_resolve_multiturn` 返回值 != 原 query）时**跳过语义缓存**（缓存 key 无法表达有态多轮语境）。
12. ✅ 多轮指代词长词抢先（缺陷10）：`_MULTITURN_REF` 改 `sorted(key=len, reverse=True)`，「那个的/它的」整体匹配不截断为「个的/的」。
13. ✅ 截空重试退避（缺陷2）：`call_llm`/`call_llm_sync` 的截空重试前加 `sleep(0.5)`（asyncio.sleep/time.sleep），避免服务端限流窗口内连续撞击。

**P1 待办（未做，需评估）**：
- 缺陷7（缓存来源过期校验）：缓存 sources 指向的 chunk 可能已删，命中后未校验存在性。可选入库时按文档 id 主动失效。
- 缺陷11（历史截断口径 6 vs 20 不一致）、缺陷9（分类/改写静默降级无埋点）、缺陷12（sync 空 content 静默当成功）——低优先级。
- provider 级熔断器（连续 N 次失败短时跳过）未做，当前跨 provider 降级已是事实熔断。

**P1 剩余项已全部完成（2026-09-08，针对性验证 + 回归 18/4 同前，零新增回归）**：
14. ✅ 缺陷7（缓存来源过期校验）：`cache.py` 新增 `_sources_still_valid()`，`lookup` 命中后校验 sources 的 chunk_id/file_id 仍存在，任一已删则视为未命中走正常检索（纯读、批量、异常降级放行）。
15. ✅ 缺陷11（历史截断口径统一）：`engine.py` 新增 `CHAT_HISTORY_LIMIT=20` 常量，`generate_chat` 用 `history[-CHAT_HISTORY_LIMIT:]`，`api/chat.py` 的 `_trim_history` 改引用同一常量，消除「6 vs 20」两套口径。
16. ✅ 缺陷9（分类/改写静默降级无埋点）：`llm_audit` 新增 `degrades` 维度 + `record_degrade(scope)`，`classify_intent_async` 回退埋点 `intent_llm_fallback`、`rewrite_query` 跑偏/异常回退埋点 `rewrite_drift`/`rewrite_error`；`degradation_rate()` 暴露 `_business_degrades`。
17. ✅ 缺陷12（sync 空 content 静默当成功）：`call_llm_sync` 耗尽仍空时 `record_degrade("llm_sync_all_empty")` + logger.warning，不再完全静默。结论：调用方（ingest_stages）已用 `if summary` 兜底，不会写脏数据，缺的是可观测性，已补上。

**P1 全部完成。剩余未动的仅「provider 级熔断器」**（跨 provider 降级已是事实熔断，额外熔断收益有限，暂缓）。

---

## 🔴 P2 批次完成（2026-09-08，三维度：前端拆分 + 检索离线评测 + MinerU 接入）

### P2-1 检索离线评测 + 数据完整性修复（意外发现）
- 写 `rrf_scale_diagnosis.py` 离线诊断四路召回 rank 量纲，**结论：RRF 四路 rank 量纲本就统一**（RRF 算法本质即 rank→分数转换，`weighted_rrf_fusion` 各源均用 `rank 0 起枚举` + `k=60` 平滑，无系统性压制）。审计报告该条属误判。
- **意外发现真缺陷**：大量「孤立 chunk: file_id=N 在 files 表不存在」→ Chroma 存在 **3736 个孤儿向量**（历史清理工程文件时绕过 delete_file 手写 SQL 删 chunks，Chroma 向量未同步删）。
- 已用现成 `cleanup_chroma_orphans.py` 清理，Chroma 向量 15926 → 12190，与 SQLite chunks 对齐（差额归零）。
- **根因确认**：当前 `delete_file`（src/storage/files.py）的 Chroma 清理逻辑本就完整（逐个 `chroma_store.delete(cid)`），孤儿是历史脏数据非当前代码 bug，无需改 delete 逻辑。

### P2-2 前端上帝组件拆分（低风险抽取，不引入 props 契约）
- 判断：GraphView(1015)/ChatView(784) 行数虽多，但大部分是 scoped CSS + 单一职责 script，并非真「上帝组件」；过度拆 Vue 组件会破坏对齐度。
- 只做「纯常量+纯函数」抽取（零响应式依赖、零 props 契约、行为完全不变）：
  - `frontend/src/views/graph/constants.js`：GraphView 的 TYPE_COLORS/STD_CATEGORY_COLORS/REL_COLORS/TYPE_LABELS/REL_LABELS/DOC_COLORS + typeColor/relColor/nodeColor/nodeLabel/nodeCategory 等纯函数。
  - `frontend/src/views/chat/markdown.js`：ChatView 的 renderMarkdown/renderAnswer（marked + DOMPurify + [编号]脚注）。
- 构建通过 5.38s，无引用错误。

### P2-3 解析层借 MinerU 深度 PDF（模块式可选接入，未安装零影响）
- config 新增 `RAG_PDF_DEEP_PARSE`（默认 "0"）。
- 新增 `src/pipeline/deep_parse.py`：`mineru_available()`（软依赖探测 + 缓存）+ `deep_parse_pdf()`（UNIPipe → mineru CLI 免底，失败/过短返回空串由调用方降级）+ `_run_uni_pipe`/`_run_mineru_cli`。
- `parser._parse_pdf` 入口接入：flag=1 且 MinerU 可用时优先深度解析，失败/过短（<200字）自动降级回 fitz+OCR 主链路。
- **当前环境 magic_pdf 未安装**，接入零副作用（mineru_available=False），现有链路不受影响。用户后续安装 MinerU + 设 flag=1 即可启用。

### 统一测试结论（2026-09-08）
- 后端全量 compileall 通过；smoke_test 18/4（4 失败仍为 LLM API key 失效，零新增回归）；前端 build 5.38s 通过。
- retrieval_benchmark 10/20：工业精确类 13 条中 10 条 PASS（连接器/镀金/Mini-fakra/导轨/轴承/标准号全对）；OA 语义类 7 条几乎全 FAIL。
- **OA 类 FAIL 根因是数据缺失**：当前知识库 96 文件里无「公文/证照/门户/人事/报表/SAP」等 OA 文档（早期 benchmark 校准时的 OA 文档已被工业文档替换），非检索算法缺陷。

---

## 🔴 CAD 工程文件「文件级索引」策略（2026-09-04，用户确认）
用户上传大量 CAD/CAM 工程文件（.mi/.step/.stp/.bdl/.pkg/.dwg/.dxf 等，共 80+ 个）导致「清理差 + 卡顿/差关机」。根因：这些是**二进制/几何坐标数据**，不是文档：
- 一个 25MB 的 .pkg 能切出 3万+ 个无意义坐标 chunk（`CARTESIAN_POINT`、数值、二进制乱码），向量化打爆 CPU/内存/API 额度。
- 例中 file 23 是纯二进制乱码（`����K...`）、file 25 是 STEP 坐标、file 17 是检测数值。
- 这些 chunk 会污染检索结果。

**用户决策（2026-09-04 追问确认）**：这些文件「也要进知识库，按文件名（图号）检索」——即不解析内容、不向量化内容，只按文件名索引。

**实现**：`src/pipeline/parser.py` 新增 `CAD_EXTENSIONS` 集合，`parse_file` 对命中类型直接返回 `[工程文件] 文件名: {stem}`（一行），不读二进制内容。这样 chunk 只 1 个（含文件名/图号），可检索且不卡。

**已清理重导（2026-09-04 完成）**：
- 已删除 27 个工程文件的 3736 个垃圾 chunk，走 import_service 从 DMS（doc_id 11-101，`/RAG测试文件`目录）重拉，新逻辑下每个只 1 个文件名 chunk。
- 结果：files 36、chunks 4618→909、dms_imports 36（27工程+9文档，映射全部重建正确）。
- ⚠️ 教训：清理时犯过一次 `DELETE FROM dms_imports WHERE file_id IN (工程文件的 doc_id)` 错误——把 doc_id 当成了 file_id，误删了文档文件的映射。dms_imports 的 file_id 是「伏羲 files.id」，doc_id 是「SeedDMS document id」，两者独立，清理时须分别用正确字段。
- chunk 内容 `[工程文件] 文件名: {stem}`，stem 仍带上游 64hex 前缀（`ff00bcf8..._图号`），图号在 `_` 后。可选优化：展示时剥离 hex 前缀。

**未办（用户暂缓）**：
- DMS 里还有约 70 个未导入文件（doc_id 11-101 中除已导入 36 个外的其余），用户后续再导。
- `.csv` 文件用 `_parse_txt`（UTF-8）读会乱码（GBK 编码），后续可加 GBK 解码。
- 文件名的 64hex 前缀（如 `ff00bcf8..._图号`）是用户上游 PDM/PLM 系统导出时自带的内容哈希，非伏羲所加。

---

## 🔴 上传报错双根因：非中文文档误杀 + trigram FTS 孤儿 rowid（2026-09-04）
用户上传 CAD/工程文件（.step/.bdl/.mi/.pkg/.csv/.log 等英文/数字内容）一直报错，根因是两个叠加 bug：

### 根因 1：语言过滤误杀非中文文档
- `chunker.chunk_text` 结尾调 `normalize_chunks()`（语言过滤器），`should_drop_chunk` 规则：长度>50 且汉字占比<0.3 且无连续中文词 → 丢弃。纯英文/数字的 CAD 文件全被丢 → 0 chunk → `_stage_embed` 报 `KeyError: 'chunks'`。
- **修复**：`chunker.py` 里 `normalize_chunks` 返回空时，回退保留原始 chunk（技术/英文内容也是有效检索内容），不丢弃。

### 根因 2：`chunks_fts_tri`（trigram FTS）孤儿 rowid 导致 constraint failed
- `scripts/reset_data.py` 和之前手动清空的 tables 列表**漏了 `chunks_fts_tri`** 虚拟表，只清了 `chunks_fts`。清空后 `chunks` 表 AUTOINCREMENT 重置从 1 起，但 `chunks_fts_tri` 残留旧 rowid（1~9699 共 6509 条），新 chunk rowid 与之冲突 → `add_chunks_batch` 报 `sqlite3.IntegrityError: constraint failed`。
- **修复**：① `reset_data.py` tables 列表补上 `chunks_fts_tri`；② `add_chunks_batch` 里两个 FTS 插入改 `INSERT OR REPLACE`（覆盖残留 rowid，防脏数据）。
- **教训**：SQLite 有两个 FTS5 虚拟表 `chunks_fts`（unicode61+jieba）和 `chunks_fts_tri`（trigram），任何清空/删除逻辑必须两个都处理，否则 trigram 表残留 rowid 会与 chunks 主表 AUTOINCREMENT 冲突。`delete_file` 已正确清理两个 FTS（按 chunk id 逐个 DELETE），唯独 reset/清空脚本漏了 tri。

---

## 前端美学升级：工业精工（2026-09-03，渐进式第一期）
用户要求「/frontend-design /fullstack-dev 完善前后端美学设计、框架设计」。确认方向：**工业精工（Industrial Precision）** + **渐进式推进**（先 tokens+登录+侧边栏，再逐页）。

### 设计语言（工业精工）
- **气质**：像工程图纸/工业终端——严谨、克制、精密感。参考方向：深青蓝主色 + 冷灰金属阶 + 暖铜橙点缀 + 等宽精密数字。
- **色彩**：主色蓝紫 `#4f6ef7` → **工业深青蓝 `#0e6e6a`**（accent），点缀暖铜橙 `#c2703d`（accent-warm），中性色从纯灰改冷灰金属阶（#fbfcfd/#f3f5f7/#e9edf1）。语义色微调：success `#1c8a5a`、warning `#c2703d`（与铜橙统一）、danger `#d14e4e`。
- **字体**：新增 `--font-display`（宋体/衬线：STSong/SimSun/Noto Serif SC，用于标题）、`--font-mono`（SF Mono/JetBrains Mono/Consolas，用于数字/代码/来源标注）、`--font-body`（系统无衬线维持）。**不引入外部网络字体（内网离线可用硬约束）**。
- **质感**：登录页脱蓝紫渐变→冷灰金属底+工程图网格纹理（linear-gradient 画 1px 网格）；登录卡顶部 3px 深青蓝 accent 线；侧边栏 logo 右下角加暖铜橙圆点（仪表盘高亮点）；nav-group-label 加细分隔线+铜橙小刻度。
- **圆角**：微收敛（radius 10→8、lg 14→12、xl 20→16），更利落。

### 已落地（第一期）
- `global.css`：tokens 全面重写（v2→v3）+ 字体栈 + body 字体 + 标题/数字/等宽字体选择器 + 登录页背景/卡片 + 两处残留蓝紫硬编码修复（message.user 渐变、tag-model/tag-material 色）。
- `MainLayout.vue`：logo 工业深青蓝+铜橙角标、sidebar-title/sub 字体、nav-group-label 细分隔线。
- 构建通过 5.7s；167 测试无涉及（纯 CSS/模板改动）。

### 已落地（第二期，2026-09-03）
- `ChatView.vue`：脚注 cite-mark（蓝紫→深青蓝+等宽，hover 铜橙）、引用卡片左侧铜橙 accent 竖条（hover 显现）、source-ref/source-loc 等宽数字、会话 active 蓝紫→深青蓝浅底、头像/气泡渐变→工业深青蓝、删除 hover 红→`--color-danger`。补全 scoped 内缺失的 .message/.message-content 定义。
- `DocumentsView.vue`：文档图标 emoji→等宽扩展名徽标（PDF/XLSX/DOC...，中性冷灰底+深青蓝字）、iconStyle 柔和色→中性冷灰、工具栏标题衬线+letter-spacing。
- 关键：ChatView scoped 里残留大量旧蓝紫 `#409eff`，与工业深青蓝冲突，已全部替换为 tokens。

### 已落地（第三期，2026-09-03）
- `GraphView.vue`：局部中心节点金色 `#f5a623`/`#d98a00`→铜橙 `#c2703d`、截断提示橙→`--accent-warm`、图例 `--bg-color`/`--border-color`(旧 token)→`--bg-elevated`/`--border`、timeline-dot 蓝灰→深青蓝、timeline-group/chunk-item 硬编码边→`--border`。图谱类型色映射（TYPE_COLORS/STD_CATEGORY_COLORS/REL_COLORS/docColors）保留多色相区分度，不动。
- `DocumentDetail.vue`：chunk 高亮锚点 `rgba(64,158,255)` 旧蓝紫光晕→`--accent-glow` 深青蓝。
- `LoginView.vue`/`SchemaResult.vue`：硬编码错误红 `#f56c6c`→`--color-danger`。
- 全站扫描残留旧蓝紫/硬编码色，真正会生效的 `#409eff`/`#f56c6c`/`rgba(64,158,255)` 已全部清除；`var(--xx, #旧值)` 形式的 fallback 因 token 已定义无害，保留（避免过度优化）。

### 待办（收尾，非阻塞）
- 渐进式三期已全部落地（一：tokens+登录+侧栏；二：ChatView+DocumentsView；三：GraphView+细节统一）。
- 剩余可做（低优先级）：ConfigView/PluginsView/McpMarket/DmsImport 的零散 fallback 旧值清理（无害）；分类颜色 category.js（Nord 北欧柔和色系）与工业精工气质兼容，暂不动。

### 重要纠偏（2026-09-03，用户反馈「细节不好看」+ 真实截图像审查）
用 Edge headless 截图（`--headless --virtual-time-budget=15000 --run-all-compositor-stages-before-draw` 才能完整渲染 SPA，预算太短会截图出「CSS 未加载的空页面」误判）+ image 模型客观审查，发现真实缺陷并纠偏：
- **衬线标题是方向性错误**：第一期主动引入宋体/衬线作标题（.login-title/.page-title/sidebar-title），实际观感「传统古典」与工业工具属性割裂。已改回无衬线，用字重(700)+字距(0.2px)体现精密感。`--font-display` 从 STSong/SimSun 改回系统无衬线。教训：工业精工的「精密感」靠字重/字距/数字等宽，不是靠衬线字体。
- **登录卡顶部 3px 绿边突兀**：无左右下边呼应、圆角衔接生硬。已删，改用品牌 logo（深青蓝方块+白字“伏”+铜橙角标圆点，呼应侧边栏）。
- **登录页输入框太简陋**：加 User/Lock 前置图标、增强 hover/聚焦光晕反馈（.el-input__wrapper:hover 边框加深）。
- **「注册」链接无区分**：拆为「没有账号？」+深青蓝可点击「注册」（hover 下划线）。
- **背景过空**：加 radial-gradient 淡深青蓝光晕（工业蓝图感）叠加网格。
- **截图误判优先级**：先确认 SPA 完整渲染（看截图 size/kb + 资源 200）再下结论；headless 旧模式 `--headless` 与 `--headless=new` 兼容性有差异，用 `--virtual-time-budget` 足够长最稳。

### 逐页精修（2026-09-03，用户要求「精修每个功能页」）
用户要求用 /frontend-design 方法论系统性精修每个功能页。核心是建立**统一页面头部规范**并消源散落内联样式：
- **global.css 新增统一规范**：`.page-header`（flex 头部 + 底部细分隔线）、`.page-title`（18px + flex + gap + 图标位）、`.page-subtitle`（12px 灰色等宽 + 工程编号感）、`.page-header-actions`（右侧操作区）。
- **ConfigView/PluginsView**：内联 `<h2 style="font-size:20px">` → `.page-title` + 图标（Setting/Grid）+ `.page-subtitle`；诊断数字 `b` 加等宽字体。
- **McpMarket/DmsImport**：`.page-sub` → 统一 `.page-subtitle`（等宽）；标题加图标（Shop/Connection）；删 scoped 里重复的 `.page-title`（20px）统一到 18px。
- **DocumentDetail**：头部 emoji 图标 → 等宽扩展名徽标（与 DocumentsView 一致）；内联 `font-size` 硬编码 → `.doc-head-*` 类；加底部分隔线。
- 全部清空残留内联大标题 + 旧 `.page-sub` 类。构建验证通过（5.77s）。

### 截图工具链教训（重要）
- Edge headless 截 SPA 登录页可直接 `--headless --screenshot --virtual-time-budget=15000 --run-all-compositor-stages-before-draw`。
- 截需登录的页面（ChatView 等）极难：hash 路由 + `router.js` 守卫用 `atob(token.split('.')[1])` 解析 JWT，headless 下 token 注入（localStorage.setItem + hash 跳转）反复失败/被璃回 login。根因：`Page.navigate` 对同 document 的 `#` 片段变化不触发 Vue Router 完整导航，且 `addScriptToEvaluateOnNewDocument` 在 headless=new 下不稳定。
- **放弃 headless 截图登录后页面，改基于完整源码精确修改**。登录页可截（无 token），其他页走代码 review。
- 后端 admin 账号密码是 `admin/admin123`（不是 `admin/admin`）。

### 登录页精细化重构（2026-09-03，用户要求 /frontend-design 精修登录页）
从「孤居中卡片」重构为**左右分栏品牌登录页**（高质感 B 端经典方案）：
- **左侧品牌区**（深青蓝渐变 + 工业网格 + 底部铜橙装饰线）：品牌 logo + 大字标题「工业文档/智能检索平台」+ 3 个特性点（混合检索/知识图谱/受控文档源，半透图标容器）+ 底部版本号（等宽 FU XI · RAG PLATFORM v1.0.0）。
- **右侧登录表单**（白卡 + 淡品牌光晕背景）：「欢迎回来」标题 + 输入框（大圆角 + focus 光晕）+ 登录按钮（品牌色 + 阴影）。
- 样式从 global.css 迁移到 LoginView.vue 的 scoped（删除 global 旧 `.login-*` 避免同名冲突）。
- **多轮截图像审查**（Edge headless + image 模型）迭代：分栏比例 1.2→1.5→收敛 1.25（视觉重心回正）；登录卡阴影加淡青内发光呼应品牌；输入框圆角从默认 small 提升到 lg 与 logo 圆润感呼应；铜橙圆点/装饰线改成半透明克制（反复被讽「突兀」）。
- **后续（2026-09-03）用户要「玻璃拟态 + 悬停动效」**：左栏三特性点改玻璃拟态卡片（backdrop-filter blur + hover 抬升/图标变色/副文案提亮）；登录卡改玻璃。
- **黄金分割分栏 + 小米式玻璃（2026-09-03）**：用户反馈侧边栏太大、登录框蓝灰无玻璃质感。改左:右 = 1:1.618（黄金分割，官网左侧小右侧大）；右侧背景去蓝灰网格→浅底+彩色光斑；登录卡改「半透明白 + backdrop-filter blur(30px) saturate(180%)」。
- **⚠️ 关键坑（Edge headless 截图）**：`--disable-gpu` 会禁用 backdrop-filter 毛玻璃渲染（截图看是实心白卡，误导判断）。截图必须**去掉 --disable-gpu**（或用 --enable-gpu）才能看到真实毛玻璃。之前连续几轮「玻璃没出来」都是这个坑导致。
- **⚠️ 毛玻璃设计原则（重要）**：backdrop-filter 要显效果，背后必须有「可被模糊的内容」——纯浅色底看不出毛玻璃。做法：登录卡背后放**清晰（不预 blur）的彩色光斑（radial-gradient 色块）**，让卡片的 backdrop-filter 真正把它们虚化；若背后用 filter:blur(40px) 先模糊了，玻璃感反而消失。

### 对话页 EasyClaw 风格重构（2026-09-03）
用户要求对话页「不要机器人头像、玻璃样式、像 EasyClaw 布局、规整统一、每个回复都有参考章节提示、输入框要精致」。

- **去头像**：删掉 `.message-avatar`（U/AI 圆形头像），改成长条角色标签行 `.message-meta-row`（「我」/「伏羲」小字 + 等宽时间戳）。
- **规整引用来源**：原 `.source-card`（堆叠大卡片+snippet）改成 `.source-row`（单行紧凑列表：[编号]+文件名+段落位置），标题「参考来源」。每个 assistant 回复都有。脚注高亮类 `source-card--highlight` → `source-row--highlight`（jumpToSource 同步改）。
- **精致输入框**：抛弃 `el-input` + append 按钮，改自建 `.chat-input-box`（玻璃容器）+ 原生 `textarea`（`.chat-input-textarea` 自适应高度 autoGrow，max 180px）+ `.chat-send-btn`（圆角品牌渐变图标按钮）。Enter 发送 / Shift+Enter 换行。placeholder 用 `inputPlaceholder` 按 mode 变化。
- **居中限宽**：消息区 `.chat-messages-inner`、输入区 `.chat-input-inner` 双向 `max-width:760px; margin:0 auto`，让内容在宽屏下居中规整（EasyClaw 风格）。
- **关键清理**：global.css 里旧对话区样式（`.message`/`.message-content`/`.message-sources span`）与 ChatView.vue scoped 重复定义，且 `.message-sources span` 会错误命中 source-row 内的 span 产生乱样式——是「布局乱」根因。已把 global.css 对话区精简为仅 `.chat-container` 基线，详尽样式全归 scoped 唯一权威。
- **⚠️ 教训**：同名类在 global.css 和 scoped 里各定义一份会互相污染（scoped 优先级更高但全局的泛化选择器如 `.x span` 会命中 scoped 内部元素）。重构前 grep 出所有同名类定义，统一收归一处。

### 🔴 关键 bug 修复：流式引用来源 __SOURCES__ 断行（2026-09-03）
用户持续报「伏羲回复 [1][2] 纯文本、无跳转键、无引用区、无布局」。根因不是前端/样式，是 **SSE 流里 __SOURCES__ 断行了**：

- `generate_stream`（engine.py）最后 `yield "\n__SOURCES__" + json`（前导 `\n`）。
- `api/chat.py` 的 `event_stream` 拼 `data: {token}\n\n`，结果 token 里的 `\n` 把 SSE 拆成两行：`data: `（空行）+ `__SOURCES__[...]`（**无 data: 前缀**）。
- 前端 `send()` 解析 `line.startsWith('data: ')` → 无前缀的 `__SOURCES__` 行被跳过 → `sources` 永远空 → `renderAnswer` 早返回不做脚注转换、不渲染引用区 → `[1][2]` 变成纯文本。

**修复**：`generate_stream` 去掉前导 `\n`（`yield "__SOURCES__"+json`）；前端 `payload.startsWith('__SOURCES__')`（去掉 `\n`）；api/chat.py 删冗余分支。

- **⚠️ 核心教训（重要）**：SSE `data:` 前缀和 payload 之间绝不能夹 `\n`——`yield f"data: {token}\n\n"` 时，若 token 自带 `\n` 会破坏 `data:` 前缀的解析。SSE 控制字符（行首 `data:`、事件分隔空行）必须由发送端用 `yield` 单独控制，业务 payload 里不要带 `\n` 前缀。
- **⚠️ 验证手法**：流式接口用 PowerShell `Invoke-WebRequest`/纯 curl 会超时或 JSON 转义出错，必须用 Python `requests` 的 `stream=True + iter_lines` 抓取，逐行检查 `data:` 前缀是否完整。

### 🔴 DMS 显示「未连接」bug：axios res.data 双层解包（2026-09-03）
用户报「DMS 显示链接失败/未连接」，但后端 `/api/dms/health` 明明返回 `logged_in:true`。根因：**前端 DmsImport.vue 全套 `res.data` 误用**。

- 后端所有 `/api/*` 统一返回 `{status:"ok", data:{...}}`，axios 的 `res.data` = `{status,data}`，真正业务数据在 `res.data.data`。
- DmsImport.vue 里 `connInfo.value = res.data`（应该是 `res.data.data`），导致 `connInfo.logged_in` 为 undefined → `connStatus` 恒 false → 顶部永远显示「未连接」。
- 同文件 7 处 `res.data` 全错（getConfig/checkUpdates/replaceAll/tree/records/health/importData）。
- **正确写法参照**：DocumentDetail.vue 用 `const {data} = await api.get(); data.data.xxx`，或 `res.data?.data`。
- **⚠️ 教训**：写新前端页面前先确认后端返回包装层（本项目统一 `{status,data}` 双层），取数是 `res.data.data` 而不是 `res.data`。新页最容易踩这个坑。
- **结论：登录页可用 3 轮截图迭代，不需要 token，是唯一能全自动真实性验证的页面；其他需登录页面无法 headless 截图（见上方截图工具链教训）。

---

## SeedDMS 接入伏羲（2026-09-03，受控文档源）
用户要求「伏羲的文件管理系统能用 E:\测试项目\SeedDMS」，本质是引入 SeedDMS 作为受控文档源头，解决「随手传文件无规划」问题。

### 已落地（已完成端到端验证）
- **设计**：SeedDMS = 规整仓库（分类/版本/审批/权限），伏羲 = 加工引擎（清洗/切片/向量化/检索/问答），单向导入管道。见 `docs/SeedDMS接入方案.md`。
- **用户决策**：① 手动勾选导入 ② 自动替换旧数据（先删数据库→再删 Chroma 向量→重新入库）③ 不回写元数据（单向）④ 预留 SeedDMS 连接配置（.env + 后端接口）⑤ 旧版本不留历史（DMS 已管版本，伏羲只留最新）。
- **新增模块**：`src/dms/`（seeddms_client.py 封装 REST API + import_service.py 导入编排 + sync_state.py 映射表读写）、`src/api/dms.py`（6 端点：health/tree/import/records/reconnect）、前端 `DmsImport.vue` + `api/dms.js` + 路由 `/dms` + 侧边栏「DMS 文档源」入口。
- **映射表 `dms_imports`**：`file_id ↔ dms_doc_id/version/content_hash/status`，UNIQUE(dms_doc_id)。用途：幂等跳过、追溯、替换识别。
- **替换顺序（关键）**：先完成新入库拿到新 file_id → 再 `replace_file_cleanup(old_file_id)`（复用 delete_file：SQLite files/chunks/FTS/entities/links → Chroma 向量 → 磁盘图片）→ 更新映射。避免「映射指向空」窗口。

### 关键坑（本轮踩）
- **SeedDMS 根文件夹 id = 1（不是 0）**：`GET /restapi/index.php/folder/0/children` 会 500，正确是 `/folder/1/children`。源码里 `getFolderChildren` 用 `if(empty($args['id']))` 判根，但路由 `/folder/{id}/children` 强制 id 必填。
- **SeedDMS 上传需 multipart**：`POST /folder/{id}/document` 用 `$request->getUploadedFiles()`，传原始 body 会报 "No file detected"。需 `files={...}` multipart。
- **SeedDMS REST API 不提供「更新文档内容」接口**：`POST /document/{id}/attachment` 传的是「附件」非「内容新版本」，`download_document` 返回的 `getLatestContent()` 不变。内容版本更新必须走 SeedDMS checkout/checkin（Web UI / op.CheckOutDocument.php / op.CheckInDocument.php）。所以伏羲「替换」靠 content_hash 变化触发，内容变化由用户在 SeedDMS 侧正规 checkin 完成。
- **replace_file_cleanup 不能置 file_id=NULL**：初次实现里 cleanup 把映射 file_id 置 NULL，导致重导时「hash 未变→skip」但文件已删（数据丢失窗口）。已改为：cleanup 只删伏羲文件数据，映射更新由 import_service 在重新入库后统一 upsert。import_service 的幂等判断加了 `old_file_exists` 校验（get_file 非 None），防映射指向已删文件的脏数据。
- **SeedDMS 内容缓存**：文档内容存 `/var/lib/seeddms/data/1048576/{docid}/`，`1.txt`=版本内容、`f1.txt`=附件。直接改磁盘文件或清 `cache/txt` 都不影响 `download_document`（它读 DB 指向的 contentDir 文件）。
- **测试数据会污染检索测试**：导入"Mini-FAKRA连接器技术规格书"测试文档后，`test_fakra_returns_process_doc_not_purchase` 失败（首条结果被新文档抢占）。清理测试数据后恢复 167 全绿。教训：往真实 DB 塞测试文档要记得清理。

### 三件事收尾（2026-09-03，配置 UI + 批量替换 + 反查）
用户追问「会不会脏肿/不稳定/性能差/结构乱」，结论：不会——DMS 是「可选外部数据源」非「核心依赖」，新增全在 `src/dms/` + `api/dms.py` + 前端 DmsImport 内，核心链路（检索/聊天/入库）零改动，唯一耦合点是 `import_service` 里 `from src.pipeline.engine import enqueue`。三件事：
1. **连接配置 UI**：不塞进现有 ConfigView 的「运行参量白名单」（那白名单明确排除密钥），而是 DmsImport 页加「连接设置」弹窗。后端 `GET/PUT /api/dms/config`，密码不回显（GET 只返回 `password_set:bool`；PUT 密码留空保留原值）。密码存 `.env`（用户选 A）。
2. **批量替换**：`import_service.check_updates()`（扫 dms_imports 逐个下载对比 hash）+ `replace_all()`（复用 import_documents）。后端 `GET /api/dms/check-updates` + `POST /api/dms/replace-all`。前端「检查新版本」按钮 +「一键替换全部」。用户显式点击触发，非后台轮询。
3. **反查入口**：后端加 `GET /api/dms/record?file_id=`（用 `get_current_user` 非 require_admin，普通用户也能看详情）。DocumentDetail.vue 详情头加「来源 SeedDMS · vN」标签，点击跳 `/dms`。

验证：GET/PUT 配置（密码留空保留原值、全空返 400）、check-updates、record 反查全部通过；167 测试全绿无回归；构建 6.13s。

### SeedDMS 环境事实（外部项目 E:\测试项目\SeedDMS）
- 容器 `seeddms`（usteinm/seeddms:latest，SeedDMS 6.0.41 + PHP 8.4 + Apache + SQLite），端口 8080，账号 admin/admin。
- REST API 入口 `/restapi/index.php`，登录 `POST /login`（body user/pass）返回 `mydms_session` cookie。
- 文档内容下载 `GET /document/{id}/content`（二进制流）；版本 `GET /document/{id}/versions`；树 `GET /folder/{id}/children`。
- 详细配置见 `E:\测试项目\SeedDMS\配置信息`（部署/挂载/备份/镜像导出）。

---

## 数据去重已完成（2026-08-28，文件级去重 + 前端美学方案）
用户要求「做一次去重处理，并制定前端美学、框架设计的优化方案」。

### 1. 文件级去重（已实际删除，非仅诊断）
- 新增脚本 `scripts/dedup_files.py`（幂等，--dry-run 预览，复用 delete_file 清理 chunks/FTS/chroma/images/实体关联）。
- 识别出 **21 对完全重复文件**（短名「(N)--xxx.docx」+ 长名「泛微...(N)--xxx.docx」content 集合完全一致），删除短名副本保留长名规范文件。
- 结果：files 63→42、chunks 9680→**8952**（释放 728 chunk）、images 5640→3455。
- 剩余「重复 content」仅 3 组共 3 冗余，且都是**同一文件内**的正常切分重叠（file 4 页码碎片、file 63 流程测试文本），非跨文件冗余，无需处理。
- 验证：health_check 7/7 通过、检索 20/20、冒烟 22/0，均无回归。
- ⚠️ 关键修正：原诊断「1460 重复 chunk」是高估——其中 728 是文件级完全重复（对 21 对），其余是文件内 chunk 边界重叠与不同数据源的相似文本，非冗余上传。

### 2. 前端美学 + 框架设计方案（文档已交付）
- 见 `docs/前端美学与框架设计方案.md`。
- 结论：设计系统 v2（tokens 已完善），重点是「逐页落实 + 响应式 + 可访问性 + 性能」四维升级，不重建 tokens。

### 3. 前端第一批优化已落地（2026-08-28，按用户决策收敛）
用户拍板：① 不做移动端 ② 图标按需引入接受 ③ 图谱有卡顿 ④ a11y 做最低档（键盘可用+焦点环）。
- **图标按需引入**：`main.js` 从 `for...of` 全量注册 250+ 图标改为白名单注册 22 个（全站实际用到的），tree-shaking 生效，主 chunk 减小。
- **vite.config.js 新建**：`@` 别名 + `manualChunks`（vue-vendor/element-plus/d3 三包）+ `/api → :8099` proxy 进仓库。构建后 element-plus 933KB、vue-vendor 111KB、d3 48KB 独立分包。
- **global.css 补齐**：语义色（success/warning/danger/info）+ 焦点环 tokens + 动效时长 tokens + `:focus-visible` 焦点可见性 + `prefers-reduced-motion` 动效降级。
- **D3 图谱卡顿优化**（GraphView.vue，Canvas 渲染）：① simulation 加 `alphaDecay(0.028)` + `alpha < alphaMin` 时自动 `stop()`；② tick 用 `requestAnimationFrame` 节流（scheduleDraw 合并同帧多次 tick）；③ `neighborSet` 遍历全部边是 O(E)，改为 `cachedHoverId/cachedNeighbors` 只在 hoveredId 变化时重算一次，draw 里直接用缓存。
- 构建通过，SPA 生产模式 200。后端检索 20/20、冒烟 22/0 不变。
- ⚠️ 全量图标清单（22 个，MainLayout/McpMarket 依赖全局注册）：Back, ChatDotRound, CircleClose, Connection, Delete, Document, Download, Folder, Grid, Loading, MagicStick, MoreFilled, Plus, Promotion, Refresh, Search, Setting, Share, Shop, SwitchButton, Upload, User。

### 4. 前端第二批：组件治理 + 分类颜色单点化（2026-08-28）
- **新建 `frontend/src/constants/category.js`**：前端分类元数据单一权威源（10 类，与后端 classification.py CATEGORY_DICT 对齐），`CATEGORY_META`（color+icon）、`categoryColor()`、`categoryIcon()`、`CATEGORY_NAMES`。
- **新建 2 个原子组件**：`components/EmptyState.vue`（icon/title/hint）+ `components/LoadingBlock.vue`（size/text），替换 6 个 view 的重复 `.empty-state` 三件套 + `.loading-center`。
- **DocumentsView 分类标签着色**：新增 `.cat-pill` 样式（白字+分类色背景），table 视图分类列 + card 视图分类 tag 都改用 `categoryColor(row.category)` 着色。
- **未做 Element Plus 按需引入**（需 unplugin-vue-components/auto-import 新依赖 + 命令式 API 样式处理，内网带宽非瓶颈风险大于收益，留待用户拍板）。当前 element-plus chunk 938KB（gzip 303KB）原因在 `app.use(ElementPlus)` 全量注册组件库，非图标。
- 构建通过（4.88s），SPA 200。
- ⚠️ 踩坑：`@element-plus/icons-vue` 没有 `Inbox` 图标（报错 `"Inbox" is not exported`），EmptyState 默认图标改 `Folder`。GraphView 替换空状态时多留了一个 `</div>` 导致 `Invalid end tag`，已删除。

### 5. 前端第三批：Element Plus 按需引入（2026-08-28）
- **方案**：`unplugin-vue-components`（Components + ElementPlusResolver）+ `unplugin-auto-import`（AutoImport + ElementPlusResolver）实现在 `vite.config.js`。移除 `main.js` 的 `app.use(ElementPlus)` + 全量 `import 'element-plus/dist/index.css'`。
- **关键处理**：删除 5 个 view 里显式 `import { ElMessage[, ElMessageBox] } from 'element-plus'`，交给 auto-import 接管（否则显式 import 会绕过 auto-import，导致命令式 API **样式缺失**，ElMessage 弹出无样式裸文字）。已标注注释。
- **效果**：最大单 chunk 从 element-plus 全量 938KB（gzip 303KB）→ d3 274KB（仅图谱页加载）；element-plus 拆分为 el-button/drawer/select 等独立小组件 chunk，按需懒加载。总 JS 1.11MB（分散小文件，首屏只加载入口 107KB + vue-vendor 169KB）。
- **⚠️ 重要环境坑（NODE_ENV=production）**：这个 Windows 环境默认 `NODE_ENV=production`，导致 `npm install` **跳过 devDependencies**（vite/@vitejs/plugin-vue/unplugin 全不装，node_modules 只剩 dependencies）。构建必须 `$env:NODE_ENV="development"; npx vite build`。之前 `npm install -D` 还误报 "removed 8 packages" 把 vite 都移除了，删 node_modules+lock 后设 development 重装才恢复。
- 构建需先手动 `Remove-Item dist` 清空（vite emptyOutDir 对旧 chunk 残留不彻底，旧 element-plus-chunk 会干扰验证）。已在 vite.config 加 `emptyOutDir: true`。
- 验证：dist 61 文件、el-message 样式/组件 chunk 独立生成、入口+懒加载资源全 200；后端检索 20/20、冒烟 22/0。

### 6. 桌面快捷方式 + 一键启动/联动关闭（2026-08-28）
用户要求：桌面创建访问伏羲的快捷方式，用指定图标，双击自动启后端+开浏览器，关浏览器自动关后端。

- **交付物**：`data/fuxi.ico`（多尺寸图标）+ `data/fuxi_logo.png`（源图）+ `scripts/fuxi_launch.ps1`（核心逻辑）+ `scripts/launch_fuxi.vbs`（无窗口双击入口）+ 桌面 `伏羲知识库.lnk`（图标 fuxi.ico）。
- **图标**：原图 285×280 非方形，Pillow 居中裁剪成 280×280 后转多尺寸 ico（256/128/64/48/32/16），保持忠实不重绘。
- **联动关闭的核心方案**：Edge 用 `--app=URL --user-data-dir=E:\更新RAG框架\data\edge_profile_fuxi` 启动**独立用户数据目录实例**（而非复用现有 Edge 会话，否则 `-PassThru`/新增进程都无法精确判断窗口关闭）。监控「命令行含 edge_profile_fuxi 的 msedge 进程是否全部退出」→ 退出则 Stop-Process 后端。已验证端到端：启动→健康200→开窗口→关窗口→自动停后端，全部通过。
- **快捷方式**：TargetPath=wscript.exe、Arguments=launch_fuxi.vbs、IconLocation=fuxi.ico。
- **⚠️ 关键坑（PowerShell 5.1 UTF-8 BOM）**：ps1 脚本含中文注释时必须存为 **UTF-8 with BOM**，否则 PowerShell 5.1 按 GBK 读文件导致中文损坏、语法解析报错（`Parser.ParseFile` 报「表达式意外标记」）。已用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($true))` 转 BOM。这是 MEMORY 已记录过的经典坑的重现。
- **快接方式参数含引号**：ps1 里 `--app="$Url"` 的转义在 Start-Process ArgumentList 里要用反引号包裹（`"` 在 PowerShell 里用 `"` 表示）。实际用 `"` 双引号包裹 URL。

### 7. 文档引用图空白 + 报错 bug（2026-08-28，d3 孤儿边炸 forceLink）
用户报「前端文档引用图没显示任何东西且报错」。根因：`get_graph_data()` 直接返回 links 全量边，而 links 表混入 131 条「孤儿边」——其 source/target 指向已删除文件（43/49/50/51/52/56）或历史 chunk_id 脏数据（7~22 等属于 file_id=1 的 chunk 级 keyword 链接残留）。**d3 v7 `forceLink(edgeList).id(d=>d.id)` 遇到无法解析的 source/target 会直接抛 `Error: node not found: N`**（非静默忽略，已用 node d3-force 实测确认），未捕获 → 整个图渲染中断空白。

- **四层修复**：
  1. `get_graph_data()`（chunks.py）加 `JOIN files fs/ft` 过滤，只返回两端都是现存文件的边。
  2. `delete_file`（files.py）补显式 `DELETE FROM links WHERE source_id=? OR target_id=?`（links 表无外键级联，之前删文件不清理链接，是链接型孤儿边根源）。
  3. 前端 `GraphView.vue` 的 `renderGraph` 加 `nodeIds.has(source)&&has(target)` 过滤防线，即使后端漏网也不崩。
  4. 一次性数据清理：`DELETE FROM links WHERE source/target NOT IN (SELECT id FROM files)`，324→193 条。

- **关键教训（重要）**：`links` 表是语义混乱的历史遗留——早期 `add_link` 存 chunk_id（keyword 链接），后来改成存 file_id（similar 文档相似度），且 `links` 无 `REFERENCES files(id) ON DELETE CASCADE` 外键。`add_link` 的 source_id/target_id 到底指什么取决于调用方，是脆弱设计。后续若重构应考虑拆分「chunk 链接」和「文件链接」两张表，或至少给 links 加外键。
- **验证**：修复后 `/api/graph` 返回 42 节点/193 边/0 孤儿边；smoke_test 22/0（含 /api/graph 断言）通过；前端 GraphView chunk 重建。

### 8. 对话意图分类失效 + LLM 超时（2026-08-28，推理型模型 reasoning 截空 + stream 端点绕过意图路由）
用户报「LLM 无法正确识别闲聊/查资料/联网 + 直接显示超时」。两个根因：

**根因 A（意图分类失效）**：前端 auto 模式走 `/api/chat/stream`（SSE 流式），但 `api_chat_stream` 端点**完全没有意图分类**——直接 rewrite_query→search。所以 auto 模式下闲聊/天气/查资料全被当知识库检索，`classify_intent` 只被非流式 `/api/chat` 路径用到。

**根因 B（超时）**：`deepseek-v4-flash`/`deepseek-v4-pro` 是**推理型模型**，reasoning_content 占用 max_tokens 预算且长度波动大（实测 213~850）。代码里 `rewrite_query`/`classify_intent` 用 `max_tokens=100` 太小→reasoning 耗尽预算→content 空→截空重试（max_tokens*3）→仍空→降级下一个 provider，三级链累积 13.8s+。实测 max_tokens=100 时 reasoning_len=299>100 导致 `finish_reason=length` content 空。

**修复**：
1. `api_chat_stream` 做 auto 意图分类（`classify_intent_async`）分流 chat/web/knowledge 三种流式。
2. `router.py` 扩充 `_CHAT_PATTERNS`（晚安/你好啊/emo 情绪）+ `_WEB_PATTERNS`（下雨/降雪/股票/赛事/热点），新增 `classify_intent_async`（规则命中 chat/web 直接返回，规则默认 knowledge 时用 Flash 快判二次确认）。
3. `rewrite_query`/`classify_intent_async` 的 max_tokens 100→2048/1024；`call_llm`（含 sync）截空重试 `max_tokens*3` 改 `max_tokens*4, 2048` 下限。
4. `handle_chat` 改调 `classify_intent_async`。
5. `api_chat_stream` knowledge 分支补 `_query_has_exact_entity` 跳过改写（与 `_handle_knowledge` 对齐，否则精确实体「镀金层」被改写散召回误导）。

**验证**：意图分类全对（晚安→chat、北京下雨→web、最近新闻→web、什么是阻抗匹配→knowledge）；rewrite 13.8s→2.7~4.7s；端到端三模式流式 3~11s 正常；smoke 22/0。

**关键教训（重要）**：
- 推理型模型（deepseek-v4-flash/pro）的任何 LLM 调用，`max_tokens` 至少要 **1024+**（reasoning 波动到 850），否则 content 被 reasoning 截空触发降级/超时。不能把「输出几个词」当成「小 max_tokens 即可」。
- `enable_thinking=False` 和 `thinking:{type:disabled}` 对 deepseek-v4 均**无效**（reasoning 照常输出），无法简单关闭推理。
- stream 端点和非流式端点**是两条独立代码路径**，改功能要两边都对齐（本次 stream 缺意图分类 + 缺精确实体跳过改写，都是和 `_handle_knowledge` 不同步导致的）。

---

## 持续性全维度调优·第二轮（2026-08-27，对话链路可观测性 + 数据冗余诊断）
接第一轮（BM25停用词/rerank持久化缓存/domain hint）。本轮落地对话链路 profile + 诊断出数据冗余。

### 4. 对话链路分阶段耗时 profile（可观测性）
- orchestrator.py 新增 `_last_chat_profile` + `get_last_chat_profile()`，`_handle_knowledge` 分阶段埋点（multiturn/cache/rewrite/search/generate/total）。
- documents.py 的 /api/health 新增第 7 项 `last_chat_profile_ms`（与 search 的 last_search_profile_ms 对齐）。
- 价值：首次剖视对话链路——稳态 chat 耗时构成 `cache 134ms + rewrite 1ms + search 1724ms + generate 6996ms`，**LLM 生成占 78%**（其次是远程 rerank）。
- 诊断：LLM 生成 7s 是 deepseek-v4-flash 推理型 reasoning_content 的固有成本，不属 bug；语义缓存已跑，重复题命中缓存。

### 5. 诊断出数据冗余（未动，需用户确认）
- **18 对重复文件**：每个「(N)--xxx.docx」短名文件在「泛微...手册(N)--xxx」长名文件里各有一份重复上传，chunk 完全一致（如 167 chunk 两文件完全重复）。
- **1460 个重复 chunk（占 9680 的 15%）**：检索时会挤占候选名额，浪费存储/检索预算。
- 未擅自删除（高风险数据操作，需用户确认保留哪份）。

### 知识库真实分类分布（重要，防过度优化）
- 仅 4 类：外购件选型(2/6455)、操作手册(58/1598)、机械设计(1/1585)、连接器(2/42)。
- 无电气自动化/材料选型/品质管理/标准件/工艺规程文档。

### 验证基线（不变）
- 检索 20/20、冒烟 22/0、单元测试 **167**（+8）。

## 持续性全维度调优（2026-08-27，用户要求不停歇持续调优）
用户要求「全维度调优、允许任何技能/专家、中途不汇报不停止」。本轮落地 3 项核心优化 + 诊断澄清。

### 1. BM25 停用词过滤（检索质量）
- tokenizer.py 新增 `_FTS_QUERY_STOPWORDS` 集合 + `to_fts_query` 过滤单字虚词/高频词（是/的/多少/层/T/G/B 等）。
- 根因：原 to_fts_query 直出全部分词，单字噪声项在大文档上无差别命中，稀释核心实义词重量。
- 验证：检索 20/20 无回归。与「规则改写不能过度扩张」教训相反——这是收敛（过滤噪声）非扩张，安全。

### 2. rerank 跨重启持久化缓存（性能，核心瓶颈）
- 根因：rerank 单次占检索总耗时 91%（1151ms/1257ms），且旧进程内缓存重启即丢。
- 解法：rerank.py 缓存 key 改为 `(query, top_k, 候选指纹)`，加 SQLite 持久化（rerank_cache 表）+ 候选指纹（chunk id 序列 md5），候选集合变化即自动失效。
- 关键设计：DB 只存排序元数据 `{id,score}`，命中时用当次 candidates 重排重建（`_reorder_by_seq`），避免缓存大文本、保证 content 实时。
- 验证：同 query 重启后从 1.4s 降到 156ms。
- ⚠️ 踩坑：第一次编辑 `async def rerank` 误删 `async` → `await` SyntaxError，服务没起来但进程占用端口，测试 8.8s 是连接超时而非缓存问题。教训：改 async 函数后先 `ast.parse` + 验证服务真起。

### 3. rerank 候选文本裁剪 + domain hint 扩展（微优化）
- rerank_with_siliconflow 文本 `[:1024]`→`[:512]`，减少远程 payload。
- `_recover_exact_match` 的 `_DOMAIN_HINTS` 扩展「工艺规程/电气自动化」两领域；曾尝试改用整本 CATEGORY_DICT 但发现宽泛词（标准/规格/材料/公差）会跨领域误命中，回退精选高区分度词（见改动注释）。

### 诊断澄清（重要，防止过度优化）
- **知识库实际只有 4 类文档**：外购件选型(2文件/6455 chunk)、操作手册(58/1598)、机械设计(1/1585)、连接器(2/42)。**没有电气自动化/材料选型/品质管理/标准件/工艺规程文档**。
- 故「屏蔽要求」「传感器接线」返 OA 手册/外购件表是「知识库无电气文档」的正常表现，非 bug；「不锈钢材料选择」命中机械设计手册也是合理（不锈钢机械性能机械手册最全）。
- 教训：调优前先核对知识库真实分类分布，否则会为不存在的文档类型加无效词典。

### 稳定性验证
- 并发 30 请求零失败；但冷查询并发下 30 个请求同时打远程 rerank 排队（平均 13s），验证了持久化缓存价值。
- 测试补到 **167**（+8：test_tokenizer_stopwords_rerank.py）。检索 20/20、冒烟 22/0。

## 全维度调优（2026-08-27，本地 embedding 权重修复 + 结构化日志）
用户选择「下载真实权重 + 全部落地 + 完整日志方案」。

### 1. 本地 bge 权重缺失修复（P0 性能，最大收益）
- 根因：`data/models/models--BAAI--bge-large-zh-v1.5` 只有 config/tokenizer，`model.safetensors` 是 **0 字节空文件**（HF 下载失败占位）。`_has_weights()` 只查文件名不查大小 → 误判「有权重」→ 每次 encode 加载失败→降级远程 SiliconFlow。
- 关键：bge-large-zh-v1.5 真实权重名是 **`pytorch_model.bin`**（非 model.safetensors）。用 hf-mirror.com 镜像下载 1.3GB 到 snapshots/79e7739.../ 目录。
- 修复：`_has_weights()` 加 `f.stat().st_size > 1024` 大小校验（过滤 0 字节占位）；删除旧的 0 字节占位文件。
- 效果：稳态检索 **~1.5s → ~130ms（提速 10x）**（首次仍 ~1.5s 是 rerank远程API首调，之后命中缓存）。本地模型加载后维度=1024、常驻内存。

### 2. 结构化日志升级（P1 可观测性）
- 新增 `src/logging_setup.py`：控制台（文本可读，保持不变）+ 文件 JSON Lines（`logs/rag.log`，TimedRotatingFileHandler 按天轮转保留14天）。
- `setup_logging()` 幂等（`_done` 标志防重复 handler）；`RAG_JSON_LOG` env 可关（默认开）。
- server.py 改用 `setup_logging()` 取代 bare basicConfig；request_logger 中间件把 trace_id/method/path/status_code/elapsed_ms 写进 logger 的 extra，供 JsonFormatter 采集。
- `logs/` 已在 .gitignore。

### 3. 确认已落地（之前误判为缺失）
- 任务持久化恢复：`recover_tasks` → `mark_stale_tasks_failed`（tasks.py）已在 lifespan 调用，重启自动把 running/pending→failed。
- request_logger 中间件（请求ID+耗时+慢查询）已存在，本轮只补了结构化 extra。
- 僵尸任务：之前 4 pending+5 running 是测试进程直接写 DB 残留，重启主服务即清零（验证到位 0）。

### 验证基线（不变）
- 检索 20/20 满分；smoke_test 22/0；health ok。

## 测试补建（2026-08-27，依据《测试补建清单.md》，P0→P2 分批补测）
用户确认「按清单 P0→P2 分批补测 + 集成测试为主跑真链路」。从 60 个测试补到 **159 个（+99）**，覆盖率 30% 达标。

### 新增测试文件（tests/）
- `conftest.py`：项目根加入 sys.path（src 包结构）；`pytest.ini` 禁用 cacheprovider（Windows .pytest_cache 权限拒绝访问）、testpaths=tests。
- `test_chunker.py`（chunker+language_filter）：分块边界/标题切分/超长拆分/空文本/繁转简/非中文过滤/表格数据豁免。
- `test_embedder_llm.py`（P0 纯函数）：_pack/_unpack/cosine/SQ8量化 + extract_json/_build_provider_chain。
- `test_engine_meta.py`（P0）：任务状态机（_emit/get_status/_evict_old_tasks/critical stage）+ 元数据层 doc_kind/authority 规则。
- `test_retrieval_meta.py`（P0 集成，跑真链路 skipif 无 DB）：FAKRA→产线工艺、镀金层→Foxconn、authority 字段、文件名召回兜底、_recover_exact_match 置顶。
- `test_chat_router.py`（P1）：意图分类/复杂度/规则改写/多轮融合。
- `test_chat_engine.py`（P1）：引用忠实度/引用提取/缓存版本。
- `test_extraction.py`（P2）：_match_word 中英文边界/标准号归一化/extract_rule。
- `test_plugins_auth.py`（P2）：RateLimiter 限流/插件 registry 读写（独立 tmp DB）。

### 覆盖情况（--cov-fail-under=30 达标 30.32%）
高覆盖：language_filter 85% / chunker 80% / classification 78% / ranking 73% / graph_recall 70% / entity_extractor 67% / search 66% / embedder 62%。
低覆盖（重 IO 需 mock/集成）：parser/ingest_stages/image_extractor/ocr_engine 0~14%，属预期。

### 关键坑（本轮踩）
- pytest cacheprovider 在 Windows 对 .pytest_cache 拒绝访问 → `pytest.ini` 用 `-p no:cacheprovider` 规避；`conftest.py` 不能用 Cache() 重设（签名需 config 参数），直接删。
- `enqueue` 是 fire-and-forget（立即起后台线程），测试不能断言 pending 且会触发真实入库链抛 KeyError:chunks 噪音 → monkeypatch `engine.threading.Thread` 拦截。
- `language_filter` 的 RAG_LANG_FILTER 是 `from config import` 模块级绑定，运行时不热更（Feature Flag 启动读，改.env重启生效），monkeypatch lf.RAG_LANG_FILTER 无效。
- 语言过滤只剔「假名/韩文/乱码」，汉字「日本語」保留，「これは日本語です」过滤后是「日本语」不满足 discard。
- 老测试 4 个文件（test_classification/test_entity_extraction/test_retrieval/test_tokenizer）用 GBK/乱码编码，新测试统一 UTF-8。
- 全量运行 `python -m pytest tests/`（根目录，保证 src 包导入）。

## 元数据层落地（2026-08-27，P1+P1.5+P2，检索 20/20 满分）
用户确认「动手 P1+P2 一起」，落地专属化工业检索精准度元数据层。

### 已落地（本轮实际交付）
1. **新字段**：`files` 表加 `doc_kind`（文档类型）+ `authority`（权威等级），`db.py` 迁移自动补列。
2. **判定逻辑**：`classification.py` 新增 `detect_doc_kind`/`resolve_authority`/`detect_doc_meta`（纯规则零 LLM）。权威等级：工艺规程(5) > 技术手册/设计规范(4) > 选型目录(3)/电气自动化(3) > 操作手册/品质报告(2) > 采购流水(1) > 未分类(0)。
   - 关键特判：文件名含「工艺/工序/装配/检测/产线/SOP」→ 工艺规程（即使分类落在「连接器」）；外购件选型类文件名含「采购/订单/供应商」或 chunk_count>1000 → 采购流水(1)。
3. **入库接入**：`ingest_stages.py` 的 `_stage_classify` 末尾检测 doc_meta 并 `update_file_doc_meta`。
4. **存储层**：`files.py` 新增 `update_file_doc_meta`/`get_file_authority`，`db.py` re-export。
5. **回填脚本**：`scripts/backfill_doc_meta.py`（幂等，--dry-run），回填 63 个文件：file_id=2 Mini-fakra产线→工艺规程(5)、file_id=65 采购数据→采购流水(1)、其余 OA 手册→操作手册(2) 等。
6. **检索消费（P1）**：`_recover_exact_match` 补查 authority/doc_kind，精确型号命中时高权威文档 priority=3、低权威=2。
7. **检索消费（P2）+ 领域强指向**：`_recover_exact_match` 新增 `domain_priority_cats`（连接器/材料选型领域词），同权威等级下连接器/材料文档优先于机械设计手册。

### 关键 bug 修复（本轮发现的真缺陷）
- **`_token_in` 的 mini-fakra 排除逻辑误伤工艺文档**：原逻辑「当 token==fakra 时，若 text 中 fakra 前紧跟 mini 则返回 False」，导致查询裸「FAKRA」时，正文主体是「Mini-Fakra」的产线工艺文档（正是权威来源）被排除不命中，而采购数据里裸「FAKRA连接器」反而命中置顶。修复：删除 mini 排除，改为极简词边界匹配。理由：query 含 mini-fakra 时 detect_exact_models 返回 ['mini-fakra']（含 mini 长 token），不会用裸 fakra 去匹配；query 是裸 fakra 时 Mini-FAKRA 恰好是最相关权威文档，应放行。

### 验证基线
- 检索基线：**20/20 满分**（镀金层厚度要求→Foxconn、FAKRA连接器规格→Mini-fakra产线 两条均修复）
- 冒烟测试：**22/0 全通过**

### 方案文档
- `docs/专属化工业环境元数据与检索精准度提升方案.md`（核心结论：不需要独立元数据库，现有 SQLite 已够，缺「文档类型权威等级」层 + 让权威进入检索排序）

## 全栈系统性缺陷检测 + 修复（2026-08-27，用户要求「全部执行」）
用户要求用前端全栈+后端设计的专业知识，跨全维度检测系统实际框架和缺陷，不局限在之前提过的领域。已完成全面代码走查（后端 65 py/8344 行、前端 4168 行），产出 20 项缺陷清单并按 P0→P2 分级，本轮已落地修复 P0/P1/P2 中的代码类缺陷。

### 已修复（本轮实际动手）
1. **#12 双融合逻辑静默丢召回源（P0 真实缺陷）**：`search.py` 非动态分支旧 `_rrf_fusion` 原只吃 bm25+vec，`RAG_DYNAMIC_RANKING=0` 时会静默丢弃 graph/filename 两个召回源。修复：给 `_rrf_fusion` 加 `graph`/`filename` 参数（固定权重：graph=向量同档、filename=max(bm25,vec)），非动态分支调用时传入。
2. **#14 死代码 chunk_count**：`_filename_recall` 的 SQL 原只 SELECT id/name/category（无 chunk_count 列），导致 `cn` 恒为 0、"小文件优先"排序意图从未生效。修复：SQL 补 SELECT `chunk_count` 列。
3. **#16 孤儿结果绕过文件均衡**：`_dedup_by_file` 原 `file_id is None` 直接 append（不受 per_file 限制）。修复：孤儿结果收集后追加在末尾，不破坏文件级均衡。另清理了 `_filename_recall` 里重复的 `if len(results) >= limit: break`。
4. **#3 history 无长度校验**：`api/chat.py` 的 `ChatReq.history` 加 `max_length=20` + `field_validator`（校验每个元素 role ∈ user/assistant/system、content 为 ≤4000 字符串）。防超长/畸形 history 撑爆 prompt 放大 LLM 成本。

### 已排除的误报（核实后无问题）
- **#10 `src/api.py` 删除**：`git status` 显示 `D src/api.py`，原担心残留引用丢端点。核实：`server.py` 唯一入口 `from src.api import router`（新包），所有路由已迁到 `src/api/`，无残留 `import src.api`。安全。
- **#3 RAG_CONTEXT_PREFIX**：原怀疑是"配置了但未接入"的假功能。核实：`chunker.py:81-89` 正确消费（`RAG_CONTEXT_PREFIX=="1"` 且 source_name 时加 80 字前缀）。非假功能。

### 保留为已接受风险（不为打磨而打磨）
- **#2 前端 token 存 localStorage**：内网部署，已有安全响应头 + CORS 收敛，风险可接受，切 httpOnly cookie 属优化非必需。
- **#5 MCP 命令注入面**：install/call 端点均 `require_admin`，stdio 用 `StdioServerParameters`（args 以 list 传入非 shell 拼接，无注入），http 传输有 SSRF 面但 admin-only。符合"admin 可信"模型，记录不修。
- **#13 语义缓存引用污染**：cache 已存原始 query 文本，0.92 阈值下相同语义意图共享 answer+sources 是内部一致的，属设计取舍。

### 重要发现：采购数据入库后出现两条新 FAIL（非本轮回归）
检索基线从 20/20 降到 **18/20**，新增两条 FAIL，但**根因是采购数据文档（file_id=65，6438 chunk，2026-08-26 17:40 入库）改变了全局词频分布**，不是本轮 4 处修复引入的回归（冒烟测试 22/0 全通过验证了无逻辑回归）。
- `镀金层厚度要求` → 期望 Foxconn，现返回非标准机械设计手册（1585 chunk 词频碾压，rerank 语义临界波动）
- `FAKRA连接器规格` → 期望 Mini-fakra 产线（仅 4 chunk），现返回采购数据（含几百条含 "FAKRA连接器" 字样的采购记录，BM25 词频碾压）
- 根因：`_recover_exact_match` 只看 chunk 内容含不含型号（`_token_in`），采购记录里也真实含 "FAKRA"/"镀金"，所以精确置顶把采购数据 chunk 也置顶了，与权威来源（产线工艺文档）竞争。这是**文档权威性/分类信号缺失**，非算法 bug。
- 可选解法（待用户拍板）：在 `_recover_exact_match` 精确命中置顶时，叠加"文档分类/权威性"信号（技术手册/工艺规程类优先于采购数据类），或对超大类采购数据做检索降权。

## 优化蓝图执行（2026-08-26，进行中）
用户定执行顺序：① 蓝图（docs/伏羲RAG系统级专业优化蓝图.md）→ ② C方案配置整合 → ③ D方案配置界面化。

### 第一批已完成（纯规则零风险，回归 19/20 无退化）
1. **检索阶段级耗时埋点**（蓝图 A）：search.py 加 `import time` + 各阶段（bm25/vector/graph/fusion/rerank/recover/total）打点，结果存模块级 `_last_profile` + `get_last_profile()`，日志输出 `检索 profile`。零副作用。
2. **查询改写规则优先**（蓝图 B）：router.py 新增 `_rule_rewrite()` 纯规则层（`_SYNONYM_EXPANSIONS` 工业同义词表 + `_REWRITE_STOPWORDS` 去停用词 + jieba 分词）。规则命中→不调 LLM；精确实体/≥2实义词→直接返回原词；规则无能为力→才交 LLM。`rewrite_query` 在 LLM 前先走规则。实测：怎么防锈→同义词展开、镀金层厚度→展开+保留实义词、GB/T 3077→精确实体直用、你好→None交LLM。
3. **health_check.py**（蓝图 C）：scripts/health_check.py 数据一致性脚本（7 项只读检查：孤chunk/chunk_count/孤entity关联/FTF行数/空embedding/孤entity_files/孤relation），退出码 0/1，可挂 cron。验证当前数据全健康。

### 命名冲突提醒（易混淆）
蓝图第一批的「C」= health_check.py 脚本；用户说的「C方案」= **配置整合**（把 77 处散落硬编码收敛到 config.py），是两个不同东西。后续执行 C方案（配置整合）时勿混淆。

### 关键事实补充
- config.py 是 UTF-8 编码正常，PowerShell Get-Content 显示乱码是 GBK 读 UTF-8 的显示问题（历史坑复用），用 edit 工具/read 工具操作安全。
- 表规模：files=62 chunks=3242 entities=506 entity_chunks=1664 entity_files=524 entity_relations=4274。
- 配置现状：.env 34 项（58 处 _env 读取）；代码里散落硬编码 77 处分布在 19 文件（rerank.py 6/engine.py 5/relation_builder 4 最多）。
- 已有基础设施：_query_terms(jieba)/detect_exact_models(型号材料正则) 在 ranking.py；RAG_ADAPTIVE 已定义。

### 第二批已完成（2026-08-26，LLM减负+健壮性）
1. **D 入库后处理合并**：ingest_stages.py `_stage_summarize` 改为一次 LLM 调用产出 JSON {summary,tags}（原 2 次→1 次），`_stage_tag` 改为消费 ctx 已有 tags 跳过；`_stage_preindex` 按 `ctx["category"]=="操作手册"` 跳过（OA 手册不预生成问答）。
2. **E LLM 调用审计**：新增 src/llm_audit.py（进程内计数器 get_stats/degradation_rate），接入 llm.py 的 call_llm/call_llm_sync（record_call/record_failure/record_empty_content/record_retry）；health 端点新增 llm_degradation_rate/llm_calls/llm_total_tokens/last_search_profile_ms。
3. **F 语义缓存版本失效**：cache.py 加 SEMANTIC_CACHE_VERSION=2，表加 version 列（ALTER 兼容），load 跳过旧版本缓存，store 写版本。

### ⚠️ 重要教训：规则改写不能过度扩张（回归 19→18→19）
- 同义词表最初把「选型/规格/参数/标准」等泛化词也做了扩张（选型→选型 选型指南 型号规格），导致「线性导轨选型」被展开引入「型号/规格」噪声词，召回偏到机械设计手册，基线上 20→18。
- 修复：同义词表只保留「真同义」（防锈≈防腐蚀、镀金≈电镀），移除泛化词扩张。验证恢复到 19/20。
- 规则：改写扩词必须是「同义」而非「关联」，例「选型」本身就是检索词不该扩成「选型指南 型号规格」。

### 第三批 + 第四批已完成（2026-08-26）
- **G query 路由显式化**：search.py 加 `_route_query()`（exact/material/semantic/general），写入 profile 的 route 字段。不改融合逻辑，仅显式化+可观测，预埋后续专项召回接入点。
- **H 引用忠实度校验**：engine.py 加 `check_citation_fidelity()`（检测杜撰编号 phantom + 模糊引用），orchestrator._handle_knowledge 在校验后打告警日志。
- **I 多轮 query 融合**：orchestrator 加 `_resolve_multiturn()`（规则式代词/省略补全，零 LLM），_handle_knowledge 接收 history，检索用融合后的 search_query_base。
- **K HyDE 兜底**：config 加 RAG_HYDE（默认 0），search.py 加 `_use_hyde`+`_hyde_retrieve`（语义模糊+BM25/图谱双零召回时 LLM 生成假设答案重召）。默认关。
- **J 检索 Stage 化重构：跳过**（用户确认风险后，提供崩溃/回归风险分析，采纳「不重构」）。理由：当前硬编码已验证 19/20，风险高无实际需求，G 已预埋 route 扩展点。

### 重要 bug 修复（第四批遗漏的 global 声明）
- search.py 的 `global _last_profile` 原本写在函数末尾，而函数前段（HyDE 处）已有赋值，导致 SyntaxError 服务起不来（0/20 全 ERR）。修复：把 global 声明移到 search() 函数开头，删除末尾重复声明。教训：Python global 声明必须在函数首次使用该名字之前。

---

## C方案配置整合已完成（2026-08-26，收敛硬编码 + 分层治理）
用户定义：C方案 = A(77处散落硬编码收敛) + B(.env 34项分层治理)，不是蓝图里的health_check脚本。

### 已收敛到 config.py 的运行参量
1. **检索参量**（最重要，在线调优候选）：BM25_RECALL_LIMIT=200 / BM25_PER_FILE=8 / VECTOR_RECALL_LIMIT=50 / GRAPH_RECALL_LIMIT=50 / VECTOR_HIGH_CONF_THRESHOLD=0.65 / CANDIDATE_K_MULTIPLIER=3 / RRF_K=60 / RERANK_TOP_K_MULTIPLIER=4。search.py 全部改为引用 config。
2. **SQLite busy_timeout 归**：SQLITE_BUSY_TIMEOUT=5000，消除 db.py/registry.py/local_cache.py/translate.py 四处重复 magic number。

### 收敛边界（用户拍板：只收重复项）
- ✅ 检索参量 + SQLite超时
- ❌ LLM max_tokens（6处各场景合理值，硬抽到config会离使用点太远、可读下降）
- ❌ 业务常量（weight=1.0 / rel_type=spec等，是业务不是配置）
- ❌ API默认值（函数签名 top_k=30）
- ❌ 网络超时（15/20/25/30/120 因场景不同）

### 教训：执行工具单轮有 100 次调用上限
- 之前长链路执行到一半触发 [TASK STOPED] [TOOL_CALL_LIMIT]。应分批执行，每批控制工具调用次数，验证后再进行下一批，避免被强制中断。

---

## D方案配置界面化已完成（2026-08-26，轻量版 D1）
1. **后端**：新增 src/api/config.py（GET /api/config 分组返回可管理配置 20 项 + PUT /api/config 改值写回 .env），白名单 _CONFIG_ITEMS 只暴露「运行参量+功能开关」不碰密钥/路径。注册入 src/api/__init__.py。require_admin 保护。
2. **前端**：新增 frontend/src/views/ConfigView.vue（分组卡片 + 开关/数字输入）+ frontend/src/api/config.js；router.js 加 /config（管理员拦截）；MainLayout 侧边栏加「系统配置」入口（Setting 图标，admin 专属）。
3. **验证**：GET 返回 4 组 20 项（检索9/缓存2/LLM1/开关8）；PUT 白名单校验拒绝改 JWT_SECRET（400）；类型校验 OK；前端 build 通过（ConfigView 2.86kB）。
4. **设计原则**：不做历史/回滚/审批流（过度设计）；修改写回 .env 重启生效（不做运行态热更，保持简单可靠）。

---

## 表格型采购数据入库修复 + RAG测试（2026-08-26）
用户上传「2026年1月-6月采购数据_供应商分类.xlsx」（18519行采购记录），发现并修复了一个真实 bug。

### 真实 Bug：language_filter 表格数据误杀
- `should_drop_chunk` 用「单 chunk 汉字占比<0.3 且长度>50」判非中文丢弃。
- 但采购表格式数据每行大量是品号/规格/订单号/日期（ASCII 数字），把中文（品名/供应商/采购员）稀释到 3%~9%，导致 257 万字符合法中文数据被整体误杀 0 chunk。
- **修复**：`language_filter.py` 的 should_drop_chunk 加豁免——文本含「连续≥2字中文词」（正则 `[\u4e00-\u9fff]{2,}`）即视为有中文语义保留，不破坏原日文/韩文/乱码过滤。

### 入库结果
- file_id=65，6438 chunk，自动分类「外购件选型」正确。
- 品名列高频：设备零件3331/邹玉兰205/以太网连接线80/气缸55/定位销51/直线导轨26/传感器23等；供应商有三铭电气/东莞怡合达/米思米/基恩士等；采购员邹玉兰/王玉娇/许丹。

### RAG 测试结论
- 带上下文的查询（供应商/采购员/采购+品名）精准命中：如「三铭电气以太网连接线」「邹玉兰采购」「采购记录里直线导轨供应商型号」均正确带引用。
- 裸词「直线导轨/传感器」默认路由到技术手册/选型目录（这些词在技术文档词频更高），需加「采购/供应商」上下文词才路由到采购数据——多源知识库合理行为，非 bug。

### 关键坑
- xlsx 上传需 multipart/form-data（urllib 手工拼 boundary）；登录返 data.token。
- 改代码后必须重启服务才生效（本次语言修复后重启才成功入库）。
- 服务进程用 Get-NetTCPConnection -LocalPort 8099 找 PID 再 Stop-Process（PowerShell 5.1 中 $pid 是保留变量不能用）。
- 入库任务状态字段是 status/stage（非 state），完成态 'done'，失败时 error 字段含异常 str（如 KeyError 显示 `'chunks'`）。

---

## 检索召回层根治：文件名+分类元数据召回兑底（2026-08-26，20/20 满分）
用户拍板根治最后两条数据问题 FAIL（系统参数/线性导轨选型），实际发现了比「重入库」更优的解法，且一并修好另两条。

### 根因（重新定位，推翻之前的「数据问题无法修」结论）
- 「系统参数在哪里设置」→ 文档正文叫「系统设置/通用设置」不含「参数设置」原词，但**文件名**「(11)--系统参数设置.docx」含「系统参数」→ 文件名不参与 BM25/向量召回。
- 「线性导轨选型」→ 「标准件新表」正文 chunk0（4507 字超大表格）含「导轨」仅 1 次被稀释，BM25 排到第 29 名进不了候选；文件名「标准件新表」又不含「导轨/线性/选型」任一词。
- 「轴承型号」「报表功能说明」同源（超大表格稀释/正文同义改写），重启后自动变 PASS。

### 解法：`_filename_recall`（search.py 新增）+ 分类兑底
1. **文件名召回**：query 实义词（jieba 去停用词）对 files.name 做 LIKE 匹配（转义 % _ 防注入），命中文件拉取全部 chunk 作第四召回源，source=filename。
2. **分类强指向兑底**：文件名无命中且 query 含「选型/型号/料号/外购件/米思米/怡合达」→ 报「外购件选型」分类的文件 chunk（hits=0，但足够进候选池）。
3. **weighted_rrf_fusion 加 filename 参数**：第四召回源权重 f_w = max(v_w, b_w)（文件名命中是强信号，不弱于最强检索源）。
4. 相关性看门改为「BM25 + 图谱 + 文件名三者均空才算零召回」。

### 关键坑（本轮踩）
- `scored.sort(reverse=True)` 元组第三元是 sqlite3.Row 不可比较 → 改用文件 id 作第三键 + key=lambda 指定。
- `recover_exact_match` 里的分类强指向（选型→外购件选型置顶）本身就够，分类兑底只需让 chunk 进候选池，score 不重要。
- 改代码后必须重启服务：之前旧进程跑旧代码导致 500/结果不一致，反复排查浪费时间。

### 最终验证
- 检索基线：**20/20 满分**（此前 19/20）
- 冒烟测试：**22/0 全通过**
- 改动文件：src/retrieval/search.py（_filename_recall + 主流程接入）、src/retrieval/ranking.py（weighted_rrf_fusion 加 filename 参数）

---

## 全面专业化打磨已完成（2026-08-26，5 方向）
基于真实数据诊断，落地 5 个打磨方向：

1. **① rerank 延迟治理**：rerank.py timeout 15s→8s（实际每次 1-2s 返回，15s 太宽会干等）。实测 rerank 仍是检索最大瓶颈（占 total 90%，1.2s），但这是外部 API 固有延迟，已有缓存 + 超时已收敛。
2. **② 可观测性前端闭环**：ConfigView 加「系统诊断」区块（依赖健康/LLM审计/最近检索耗时 profile），api/config.js 加 health()，后端 /api/health 已含 llm_calls/degradation_rate/last_search_profile_ms。
3. **③ 配置运行值提示**：api/config.py 白名单加 attr 字段，GET 返回 runtime_value + pending_restart（区分「运行值 vs .env 值」），前端「待重启生效」标签。修了 .env 无显式值时 value 回退 runtime 的 bug。
4. **④ route 半成品收尾**：判断为「非半成品」——route 打点（可观测）与 get_dynamic_alpha 调权重（行为）是两套分工，强行合并反而损失信息。保留。
5. **⑤ 异常日志补齐**：判断「已足够规范」——剩余 except:pass 都是清理/可选优化类，静默合理，不为补而补。

### ⚠️ 重要诊断：线性导轨选型 回归的真相（数据问题，非本轮打磨引入）
- 打磨后回归 18/20（线性导轨选型 + 系统参数 FAIL），需澄清不是本轮引入：
- 「线性导轨选型」正确文档「标准件新表.xlsx」是表格，「导轨」仅 1 chunk、「选型」0 chunk；而「机械设计手册」1585 chunk 词频碾压 → BM25 召回标准件新表直接缺席，向量仅微弱领先(0.554 vs 0.552)，rerank 语义临界波动。与「轴承型号」同源但更极端。
- 本轮唯一改的检索是 rerank timeout 15→8s，不影响排序（验硅流仍正常返回）。
- 结论：该 FAIL 属数据分布问题，解法是重新入库标准件新表（补全内容/分类指向选型）。

### 验证基线
- smoke_test.py：22/0 全通过。
- 检索回归：18/20（线性导轨选型 + 系统参数均数据问题）。

---

## 全维度系统化优化（2026-08-25，进行中）
用户要求「全维度、全铺开」的深层次优化，安全类暂不处理（防火墙/改密码/备份）。
方案文档：docs/系统级深层次优化方案.md。

### 安全运维硬化（2026-08-26，用户确认：不做备份脚本，管理员密码保持不变，其他安全可优化）
- **安全响应头**：server.py 新增 security_headers 中间件（X-Content-Type-Options nosniff / X-Frame-Options SAMEORIGIN / Referrer-Policy / X-XSS-Protection），零风险已落地。
- **防火墙规则**：`伏羲RAG-8099` 现有规则 RemoteIP=Any（全网段可访问）是唯一真实暴露面，用户已拍板收紧到「内网网段」，但 netsh 修改需管理员权限（本会话 elevated 不可用 runtime=direct，需用户手动跑或开提权）。目标规则：`netsh advfirewall firewall set rule name="伏羲RAG-8099" new remoteip="172.25.30.0/24,127.0.0.1"`。
- **已有安全基线（勿重复做）**：bcrypt 慢哈希 + sha256→bcrypt 平滑升级；登录/注册限流 5次/60s；CORS 已收敛具体来源（非 *）；JWT_SECRET 64字符 + 4个API key 已 gitignore；健康检查四维；繁简归一化 0 繁体残留；嵌入重建完成（0 空 embedding）。
- 实际内网网段：**172.25.30.0/24**（以太网 172.25.30.11），另有 VMware/vEthernet 虚拟网卡（192.168.x、172.26.x）。

### 知识图谱三维补全（2026-08-26，纯增量，零回归）
用户选做「知识图谱补全」：入度/出度 + 局部图谱 + Timeline 时间轴。

#### 后端改动
- **入度/出度拆分**：`src/storage/entities.py` 的 `get_entity_graph` 节点新增 `in_degree`/`out_degree`（有向：out=作为 source 指出，in=作为 target 被指向）+ `created_at`。新增 `get_entity_degree(entity_id)` 轻量函数；`api_entity_detail` 也补入度/出度。
- **局部图谱 `get_entity_local_graph(entity_id, hops=1~5)`**：BFS 取 N 跳邻域，返回与全图同构的 {nodes,edges,center_id}，边去重。新增端点 `GET /api/entities/{entity_id}/graph?hops=N`（graph.py，放在 `{entity_id}` 之前避免路由遮蔽）。
- re-export：`get_entity_local_graph`/`get_entity_degree` 从 entities.py → db.py。

#### 前端改动（GraphView.vue）
- 反链面板：实体详情加「连接度 总/入/出」+「入库时间」行。
- 「展开局部图谱」按钮 + 跳数选择（1/2/3），`expandLocalGraph()` 拉局部数据→切 entity 模式→默认 hideCooccur→renderGraph。
- 顶部 tab 新增第三个视图「时间轴」（mode='timeline'）：`fetchTimeline()` 按 created_at 分组（到分钟），渲染时间轴 chip 流。
- 图标 import 增加 Share/Connection；新增 `.timeline-view` 样式。

#### 验证
- 前端 `npm run build` 通过（GraphView 编译无错）。
- 端到端：M8（连接器）局部图谱 1 跳=188节点/187边；实体详情 degree=187 in=1 out=186（连接器是主动指出 186 关联的枢纽）；全图节点带 created_at。

#### 关键发现
- 图谱高度稠密连通（M8 的 2 跳邻域=362节点/占全库72%，因 cooccur 边太多），局部图谱默认 hideCooccur 是必要的。

### 图谱功能进一步优化（2026-08-26，第二轮）
用户继续要求「优化图谱功能」，本轮落地三件事：
1. **局部图谱节点上限保护**：`get_entity_local_graph` 加 `max_nodes=200`，邻域超限时按「中心 > 语义边(非cooccur) > 度数」排序截断。返回带 `truncated`/`total_nodes`（完整邻域真实大小）。端到端：M8 2跳 362→150 截断，中心保留。API 加 `?max_nodes=N`。
2. **中心节点高亮 + 放射性布局**：前端 `renderGraph` 里 `localCenter` 节点金色描边 + 光晕 + 加粗标签，`forceX/forceY` 对中心节点加大引力（strength 0.5）形成核心-外围布局。
3. **返回全图入口 + mode 覆盖 bug 修复**：局部图展开时 `mode.value='entity'` 会触发 `watch(mode)→fetchGraph` 拉全图覆盖局部数据（真实 bug），用 `suppressModeFetch` 标志抑制；顶部加「返回全图」按钮（`backToFullGraph`）。
- 图标 import 增加 Back；`expandLocalGraph` 提示带截断信息。

### 图谱全链路深度优化（2026-08-26，第三轮：生成/使用/存储）
用户要求深入优化图谱的「生成、使用、存储」全链路，诊断发现 4 个真实数据层问题，全部落地：

#### 1. uses_standard 边补建（材料→采用标准，核心知识缺失）
- 根因：`_build_uses_standard_edges` 用「同句共现」，结果 0 条（材料名和标准号很少同句）；纯「同 chunk」又会误联（不锈钢→GB/T 5782 等 65 个紧因件标准）。
- 修复：改为「只联 standard_domain='材料' 的标准号」，82 对候选筛出 8 对高质量边（不锈钢→GB/T 1220/700，铜合金→GB/T 12232/12234）。
- 补跑：`scripts/backfill_uses_standard.py`（幂等）。uses_standard 0→8。

#### 2. 实体抽取覆盖提升 + 中文材料词 `\b` bug（重头）
- **关键 bug**：`extract_rule` 材料匹配用 `\b` 边界，对中文材料词（铜合金/黄铜/不锈钢/陶瓷/磷青铜）**永远匹配不到**（`\b` 对中文无效）。导致中文材料实体只被 LLM 部分抽取，关联严重缺失（不锈钢含 60 chunk 只联 27）。
- 修复：新增 `_match_word()`——纯 ASCII 词用 `\b`，含中文词用子串匹配。中文材料词全部恢复抽取。
- **不锈钢牌号归并**：SUS304/1Cr18Ni9/316L 等归并到「不锈钢」material（aliases + variants）。
- **参数扩充**：新增絶缘电阻/介电常数/耐压/爬电距离 4 个 param 模式。
- 补跑：`scripts/backfill_rule_entities.py`（INSERT OR IGNORE 幂等，不堆叠 mention_count）。关联大幅补齐：铜合金 3→34、黄铜 15→31、不锈钢 27→128、陶瓷 1→4、磷青铜 3→8。

#### 3. cooccur 噪声治理（展示层，不删数据）
- 前端 draw() 里 cooccur 边透明度按 weight 对数归一化（weight=1→0.04，60→0.26），高权重共现更实，低权重噪声更淡。
- graph_recall 仍只走强语义边（spec/uses_standard/compatible_process），不走 cooccur，已是正确设计；uses_standard 补齐后它有数据可用了。

#### 4. 实体同义归并/别名
- 新增 `ENTITY_ALIAS_MAP`（材料全称↔俗名/牌号，工艺同义词），在 `normalize_entities` 里自动补 aliases。
- 验证：PA66→尼龙66/尼龙 66；镀金→金镀层/镀金层；不锈钢→SUS304/1Cr18Ni9。

#### 关键教训
- 短英文缩写扩充（PC/PU/PPO/POM）需谨慎：`LIKE '%词%'` 会大量误报（PC 命中 Prepare、PU 命中等式里的比压符号）。只有带上下文的牌号（SUS304）确有价值。
- 优化前必须先用 SQL 核对真实数据（内容含词数 vs 关联数），否则会加一堆命中不到的词典或误判。

### 已落地的优化（第一批，检索链路）
1. **模型预热**：server.py lifespan 加 `_warmup_embedder()`（后台线程加载 bge 模型），解决首个请求冷启动 7.32s 阻塞 → 20s 超时。验证：首条 query 从 read timeout → 正常 PASS。
2. **rerank score 字段统一（架构性 bug）**：之前 rerank 返回顺序按 SiliconFlow relevance 分排，但 `score` 字段残留 post_rank 的 boost 大数（5~27），导致「排序与分数脱节」。修复：rerank_with_siliconflow / rerank_with_deepseek 把 rerank 分写入 `score` 主字段，原值保留在 `_pre_rerank_score`。
3. **精确命中保护 `_recover_exact_match`**：search.py 在 rerank 之后、hook 之前新增，把「型号/文件名词精确命中」的结果用词边界匹配（`(?<![a-z0-9])token(?![a-z0-9])`）保护性 boost 拉回前方（score 量纲 0~1，boost 0.2~0.45）。含 fakra/mini-fakra 子串排除。
4. **FAKRA 型号匹配修复**：ranking.py `detect_exact_models` 修复 fakra/mini-fakra 子串重复命中——命中 mini-fakra 后剔除裸 fakra；裸 fakra 用 `(?<![a-z0-9])fakra` 避免匹配 mini-fakra 里的子串。
5. **召回扩池**：search.py BM25/向量/图谱 limit 30→50，中间候选池 top_k*3，给目标小文档进 rerank 机会。
6. **文件级去重 `_dedup_by_file`**：每 file_id 最多保留 3 个 chunk，防大文档夸占 top。
7. **上下文截断**：engine.py `_build_reference_context` 单 chunk 800 字、总 6000 字，省 token。
8. **分类加权真正生效（bug）**：ranking.py `dynamic_category_weight` 原来 r.get('category') 永远取不到值（结果无 category 字段）=死代码。修复：按 file_id 批量补 category + 强指向词优先级（型号/选型→外购件选型 +4，机械设计 -1）。

### 回归基准脚本（新增）
- `scripts/retrieval_benchmark.py`：可重复运行的 20 条权威基线（期望已 SQL 校准），用法 --base --save。

### 检索质量基线（20 条工业+OA 用例）
- 优化前：14/20（首条还 20s 超时）
- **主链路系统重构后：19/20**（仅剩 1 条为数据质量问题，非算法）
- 基准脚本：scripts/retrieval_benchmark.py；数据：data/retrieval_baseline_authoritative.json

### 主链路系统重构（2026-08-25，关键技术突破）
按 AI 专家（ai-engineer-1）诊断方案实施，根治了「score 字段四类分数混加导致跷跷板」：
1. **召回层 per-file 截断**：BM25 limit 200 → 召回后立即 `_dedup_by_file(per_file=8)`，从根上破解大文档词频碾压，小文档 chunk 真正进候选。
2. **撤销 rerank 层 doc length norm**：`_apply_doc_length_norm` 停用（长度惩罚错位，应属 BM25 层），rerank 只用语义分。
3. **`_recover_exact_match` 从加法改为置顶保序**：不再 `score += boost`，改为离散优先级（2=型号精确命中 / 1=文件名命中+分类强指向 / 0=普通），稳定重排不动 score 数值，杜绝跷跷板。
4. **rerank top_n 扩大**：`rerank(query, merged, top_k=max(top_k*4,top_k))`，让目标文档不被 rerank 的 top_n 提前淘汰，之后 `_recover_exact_match` 置顶 + 截断到 top_k。
5. **文件名命中优先（OA 手册关键）**：query 实义词（去停用词）命中文件名→置顶，解决 OA 手册内容同质化下语义分不开的问题。

### 剩余 1 条 FAIL 的根因（数据质量，非算法）
- `系统参数在哪里设置`：期望文档 (11)--系统参数设置.docx(file8) 和 file17，内容里根本不含「系统参数/参数」（各仅 5 chunk 的版权/空文档，疑似上传解析失败）。全库仅 2 chunk 含完整「系统参数」且在其他文档。**需重新入库修正文档，检索算法已到实际天花板。**

### 主链路生成层修复（2026-08-25，第二轮）
检索修到 19/20 后，发现「对话链路」（/api/chat）返回错误，逐一修复：
1. **LLM 空 content bug（关键）**：`call_llm`/`call_llm_sync` 在 max_tokens 过小（<200）时，deepseek-v4-flash/pro 是推理型，reasoning_content 抢占 token 导致 content 空、finish_reason=length。修复：检测 `finish_reason==length 且 content 空` 时自动 `max_tokens*max(3,512)` 递减重试。验证：max_tokens=50 的 query 从空→正常返回。
2. **精确实体查询跳过改写**：`orchestrator._query_has_exact_entity()` 检测 query 含型号/标准号/材料/中文参数词（阻抗/厚度/镀金等，注意不能用 `\b` 对中文无效），命中则跳过 rewrite_query，避免「镀金层」被改写成「镀金 厚度 要求」拆散后召回机械手册。
3. **语义缓存污染**：清空了 semantic_cache 表（9 条旧错误答案，是检索修复前缓存进去的，检索修好后命中旧缓存导致对话返回错误）。

### 重要教训（执行层）
- **改代码后必须重启服务**：之前几次测试「改了不重启」导致用直接 python 跑（新代码）和 HTTP 请求（旧进程）结果不一致，浪费排查时间。
- **语义缓存要带失效机制**：检索逻辑大改后，旧缓存答案会污染对话结果，需手动清空或加版本号。

## 存储 + 可观测性优化（2026-08-25，P1 维度）
1. **补联合索引**：db.py SCHEMA 加 `idx_chunks_file_idx ON chunks(file_id, chunk_index)`。EXPLAIN 确认按 file_id 读 chunk 排序走索引无临时排序。
2. **健康检查增强**：/api/health 从只返回 status 改为依赖健康度（db/chroma/llm_configured/disk_free_gb），核心依赖 db 不可用时降为 degraded。
3. **请求日志增强**：request_logger 中间件加 X-Request-ID（上游传入或生成）+ 慢请求告警（>3s 打 WARNING）。
4. **确认任务持久化已存在**：engine.py recover_tasks 已实现（启动加载历史任务 + mark_stale_tasks_failed），AGENTS.md 里「任务 in-memory 丢失」是过时描述。
5. **数据完整性核查通过**：0 孤儿 chunk、0 chunk_count 不一致、0 孤儿 entity_chunks。

### 主链路最终状态（2026-08-25）
- 检索基线：19/20（仅剩 1 条是数据质量问题，非算法）
- 对话链路：修复 LLM 空 content + 精确实体跳过改写 + 清空语义缓存后，镀金层/接触电阻/轴承型号/公文流程均正确返回来源+回答。
- 检索架构重构：召回(per-file截断) → 粗排(RRF) → 精排(rerank置顶保序，非加法)。

### 路线B 文档长度归一化（已落地，2026-08-25）
- rerank.py 新增 `_apply_doc_length_norm()`：file_id → 总 chunk 数 → penalty=1/(1+0.5*log(1+cnt))
- 1585 chunk（机械手册）→ penalty≈0.21，38 chunk → 0.79
- 在 rerank() 统一入口对三条路径结果都应用；原分保留 _rerank_score，penalty 存 _doc_norm_penalty
- **效果有限的根因**：FAIL 案例（轴承/镀金）是「大文档 264 chunk vs 小文档 2 chunk」的词频碾压，发生在 BM25 召回层（目标小文档根本进不了 top30），rerank 归一化救不回来。

### 重要发现（尚未解决，需用户决策）
**rerank（SiliconFlow BGE-reranker）的语义偏好与「正确文档」不一致**：
- `轴承型号`/`镀金层厚度要求` 两条，机械设计手册 rerank 分高达 0.99/0.95，而正确文档（标准件新表/Foxconn）才 0.016——差 60 倍，boost 拉不回。
- 根因：机械手册篇幅大词面广，reranker 给的语义分反而最高。
- 这是 rerank 模型的语义判断问题，非排序代码可修。

### 剩余 5 个 FAIL 的深层归因（2026-08-25）
1. `轴承型号`：机械手册 264 chunk 含「轴承」vs 标准件新表仅 2 chunk → BM25 词频碾压，标准件新表进不了 top30。**召回层 gap**。
2. `镀金层厚度要求`：类似，跨文档语义 gap。
3. `FAKRA连接器规格`：reranker 判「FAKRA连接器规格」更接近 Mini-fakra 产线工艺文档（内容大量讲连接器规格），语义偏好问题。
4/5. `系统参数`/`报表`：OA 同类手册文本高度相似，rerank 分难区分。

### 固定基线的 20 条用例（data/retrieval_baseline 系列）
工业精确（期望正确文档）：接触电阻/FKARA/Mini-fakra 参数/材料 LCP PA66/标准号 GB-T-3077/外购件导轨轴承；OA 语义（期望对应手册）：公文/证照/系统参数/门户/人事/报表/SAP。

### ⚠️ 基线期望标注错误的重要自我纠错（2026-08-25）
用 SQL 核对知识库真实内容后发现，20 条基线里「期望文档」有好几处是拍脑袋编的，与实际不符：
1. **`FAKRA连接器规格` 期望=Foxconn 是错的**：Foxconn(file1) 内容里根本不含 'fakra' 词（0 chunk），全库只有 Mini-fakra 产线(file2)含 3 处 fakra。检索返回 Mini-fakra 是正确行为。
2. **`系统参数在哪里设置` 期望=系统参数文档 命中不了**：目标文档 (11)--系统参数设置.docx(file8) 内容里竟不含「参数」二字（0 chunk，可能扫描/OCR 质量差或内容极少），文件名有「系统参数」但内容没有。
3. **`报表功能说明` 期望=报表文档**：实际含「报表」二字的文档多达 6+（资产/微博/会议/人事/预算都含），正确文档 报表.docx(file59/60) 无法靠纯内容区分。
4. **`轴承型号`**：机械手册 264 chunk 含「轴承」vs 标准件新表 2 chunk，BM25 词频碾压，属数据分布问题。

教训：优化前必须先用 SQL 核对「期望正确答案」是否真存在于知识库，否则会用错误靶子越调越歪。

---

## 系统级优化：检索质量 + 繁简归一化修复（2026-08-24）
用户要求「全维度检测+系统级优化」，实际发现了检索质量真实缺陷并修复。

### 真实根因（重要教训）
- **检索质量差的真凶是数据层繁简不一致，不是检索算法**：历史入库的 Foxconn 繁体 OCR 文档 78% chunk 含繁体字，而简体查询无法命中繁体内容（如「镀金」查不到繁体「鑑金」）。
- 之前误判是 `rewrite_query` 查询改写问题——实际 direct /api/search 不走 rewrite，但召回仍错，证明是数据层。定位方法：直接 SQL 查「镀金」vs「鑑金」在哪些文件（镀金在 file4，鑑金在 file1）。
- `language_filter.py` 手工映射表 `_SAFE_TC_TO_SC` 缺 86 个工业金属/化学繁体字（鑑/镍/铬/锌/锡...），导致「保守策略」反而损害工业检索。

### 已实施的优化（6 项）
1. **繁转简引擎升级**：安装 `opencc-python-reimplemented`，`normalize_han` 优先用 OpenCC t2s（覆盖 86 个工业字），保留歧义字保护集 `_AMBIGUOUS_TC`（乾/髮/後等一简多繁字用占位符保护还原）。
2. **rewrite_query 安全网**：改写后与原 query 分词重叠度 < 0.4 则放弃改写用原 query（`_token_overlap` + `_REWRITE_OVERLAP_THRESHOLD`）。
3. **rewrite_query 结果缓存**：进程内 OrderedDict LRU（`_rewrite_cache`，max 256），同 query 不重复调 LLM。
4. **rerank 结果缓存**：`(query, top_k)` LRU + 5min TTL（`_rerank_cache`），实测同 query 第二次 1.66s→0.76s。
5. **连接管理统一注释**：`db.py _get_conn` 明确 contextvars(请求)→threading.local(后台线程) 优先级；`reset_conn()` 清 contextvar。
6. **消除幽灵配置**：`router.py` docstring 更正（RAG_INTENT_LLM 未启用，纯规则分类）。

### 关键脚本
- `scripts/normalize_traditional_chunks.py`：存量 chunk 繁转简重建（--dry-run/--skip-embed/--embed-only/--limit）。
- 存量 3242 chunk 中 78 个有繁体变化（转换 2073 字符），文本+FTS 已重建，向量重建（embed-only）后台跑 40-50 分钟。

### 待办/注意
- 向量重建后 content 变简体需同步重建 embedding，否则向量仍是繁体语义（实测 BM25 已对但向量+rerank 仍偏）。
- `opencc-python-reimplemented` 已加入依赖，需更新 requirements.txt。

---

## MCP 市场按 RAG 提升度排序（2026-08-20）
用户要求：MCP 市场列表按「对 RAG 知识库文档管理系统最有提升效果」从大到小排序（原先是 useCount 排序）。

### 改动
- 新增 `src/mcp/relevance.py`：`score_server(qname, display, desc)` 关键词加权评分
  - DIMENSIONS 能力维度：向量语义检索(8)、文档文件处理(8)、检索增强问答(9)、网页搜索联网(7)、数据库存储(6.5)、知识图谱实体(7.5)、学术文献(5.5)、代码文档(4.5)
  - 每维度命中任一关键词计一次权重（不按关键词数叠加，防刷分）
  - FEATURED_BOOST 明星工具显式加权（brave/exa/filesystem/pinecone/qdrant/milvus/chroma/weaviate 等）
- `local_cache.query_local` 加 `sort_by_relevance=True` 参数：拉全量（命中搜索的）后 Python 打分排序再分页，返回值带 `relevance` 字段
- `smithery.list_servers` 传 `sort_by_relevance=True`
- 前端 McpMarket.vue 卡片 meta 区加「相关度 X」标签

### 关键教训（关键词去噪声）
- 营销套话必须剔除：`grounded/grounding`、`citation`、单 `entity`、单 `github`（几乎每个 server 描述都带 github 链接，是最大污染源）
- 用短语而非单词：`entity extraction` / `entity relationship` 而非单 `entity`；`code search`/`api documentation` 而非单 `github`
- Smithery registry 描述质量参差：部分小众 server 用营销套话（RAG/knowledge/grounded）刷高分，纯关键词无法完美区分，但整体排序方向正确（文档/搜索/向量/学术靠前，天气/地铁/加密沉底）
- 分页：query_local page_size 上限 50（min(max(1,page_size),50)），前端分页正常

## 分类体系重构：操作手册 + 分系统文件夹（2026-08-20）
用户痛点：泛微 OA 操作手册（28 个模块）被工业题材分类词典误判得七零八落（公文→标准件、门户/邮件/车辆→外购件选型、证照/报表/通信→品质管理）。已修复：

### 关键改动
- `classification.py`：新增「操作手册」一级分类（MANUAL_CATEGORY）+ 系统名识别（detect_system_folder / SYSTEM_FOLDER_MAP：泛微OA/钉钉/企业微信/飞书）
- `is_manual()`：判断操作手册，但带 MANUAL_EXCLUDE_KEYWORDS 排除「设计手册/技术手册」等工业手册
- `ingest.py _auto_classify`：改为「文件名优先，正文兑底」——文件名能判出分类就直接采用，裸文件名才拼正文；并把「机械设计」提到「标准件」前面
- 「标准件」宽泛词特殊处理：文件名含标准件但正文带供应商/型号/图片/链接特征时，归「外购件选型」（如「标准件新表」实际是米思米/怡合达选型目录）
- `_auto_folder`：操作手册按系统名建虚拟文件夹（/泛微OA），实现「每个系统的操作手册一个文件夹」
- `ingest_stages.py _stage_classify`：分类后追加 folder 写入
- `scripts/reclassify_manuals.py`：批量重分类存量文件（--dry-run 预览）

### 结果
- 58 个文件归「操作手册」+「/泛微OA」，分类分布：操作手册58/连接器2/外购件选型1/机械设计1
- 工业文件（Foxconn连接器手册、非标准机械设计手册）不受影响

### 关键教训
- **文件名优先但要防误导**：工业文档的"标准件"等词依赖正文，不能只看文件名；"设计手册"是工业手册不是办公手册
- 已入库数据改分类后需 `scripts/reclassify_manuals.py` 迁移，或 delete_file 重传

## 第十五轮续：文件管理四项补齐（2026-08-19）
用户要求四项「全做」：目录树+双向链接 / AI智能找文件 / WPS查看编辑 / Markdown可读模式。已交付：

### 1. 目录树 + 双向链接
- files 表新增 `folder TEXT` 列（虚拟目录，默认 `/`，迁移 `_migrate_add_column`）
- db.py 新增：`update_file_folder` / `normalize_folder` / `list_folders`（递归建目录树含 count）
- `list_files`/`list_files_with_entities` 加 folder 参数（根目录精确匹配、子目录前缀匹配 `LIKE '路径/%'`）；`add_file` 加 folder 参数
- API：`GET /api/folders`（目录树）、`PUT /api/documents/{id}/folder`（移动文件，admin）
- 双向链接（backlinks）上一轮已落地，本轮未改
- 前端 DocumentsView：左侧改为「AI找文件框 + 目录树(el-tree) + 分类」，文件卡片加「移动到」下拉

### 2. AI 智能找文件
- API `POST /api/files/find`：语义检索(search top30) → 聚合到文件级(hits累计+最高score+snippets) → LLM 生成推荐理由（失败降级空）
- 复用 `_call_deepseek` 生成推荐理由（严格 JSON，正则提取）
- 前端：DocumentsView 顶部「AI 找文件」输入框 + 结果抽屉(el-drawer)

### 3. WPS 查看/编辑（方案2：零成本本地唤起）
- 没有 WebOffice 凭证，先落地「用 WPS 打开」本地 URI 唤起（wps:// 协议）
- WebOffice 内嵌编辑需申请 AppId/AppSecret（付费），留接入位，未做
- 前端 DocumentDetail：「用 WPS 打开」按钮（window.open wps://）+ 降级提示

### 4. Markdown 可读模式
- 新增 `src/pipeline/markdown_render.py`：`chunks_to_markdown`（去噪声+分章节+代码/表格/列表分类渲染）、`render_chunk_readable`
- API `GET /api/documents/{id}/markdown`：返回拼接的完整可读 Markdown
- 前端 DocumentDetail：新增「章节视图/可读模式」切换（read 模式按需加载完整 md 文档流）+「导出 Markdown」下载

### 关键教训/坑
- **Windows PowerShell 5.1 的 Invoke-RestMethod `-Body` 默认不 UTF-8 编码中文**，发中文 query 会 422/乱码；须用 `[System.Text.Encoding]::UTF8.GetBytes(...)` 作为 -Body 或加 -ContentType charset
- **search 端点本身无 bug**：之前误判「search 返回 0」是因为测试脚本中文编码问题 + 服务进程日志显示前端发的 query 正常返回 5 条
- 登录返回结构：`data.token`（不是 access_token）；admin/admin123
- 目录树是「虚拟目录」：基于文件 folder 字段派生，无独立文件夹实体；「新建文件夹」实际是选中目标路径，上传归入（前端 createFolder 只是设 activeFolder 并提示）

## 第十五轮续2：图片纳入入库 + .md 无损化（2026-08-19）
用户拍板：做「图片纳入管线（Obsidian ![[图]]）」+「.md 可读模式无损化」。已交付：

### 图片提取（可读模式 ![[图]] 显示）
- 新增 `src/pipeline/image_extractor.py`：从 PDF/PPTX/PPT/XLSX/DOCX 提取图片（-PPT 旧版走 LibreOffice 转 pdf）
- db.py 新增 `images` 表 `{id,file_id,page,path,filename,width,height}` + `add_images`/`list_images`/`count_images`；`delete_file` 级联删图片记录+磁盘目录
- engine 挂 `images` async stage（Feature Flag `RAG_IMAGE_EXTRACT=1`，幂等：已有图片跳过）
- config 新增 `IMAGES_DIR=data/images`
- API：`GET /api/documents/{id}/images`（图片列表）；server.py 挂载 `/images` 静态目录
- 前端 DocumentDetail：可读模式底部「图片网格」，点击预览弹窗
- 补跑脚本 `scripts/backfill_images.py`（历史文件补提图片）

### .md 无损化（Markdown 源可读模式无损渲染）
- chunker `chunk_text` 对 .md 源给 chunk 加 `markdown: True` 字段；入库 metadata 存 `markdown` 布尔
- 前端：isMarkdownSource 判断，md 源在章节视图直接渲染原始内容（不走 cleanContent 页眉页脚过滤）

### ⚠️ 关键坑（本轮踩坑）
- **扫描件 PDF 图片提取爆炸**：扫描件每页的图片对象=整页扫描（982x1450 覆盖全页），1423页→844MB。修复：用 `page.get_image_bbox(img)` 判断图片显示 bbox 是否覆盖整页（cover_w>0.9 且 cover_h>0.9）跳过头；非标准手册从 1423 张→8张真插图（5MB）
- **.ppt 旧版(OLE) 图片提取**：python-pptx 不支持，需 LibreOffice 转 pdf；且转出的每页元素都光栅化成图，Foxconn 提了 2006 张。修复：`MAX_IMAGES_PER_FILE=500` 上限，按图片面积降序截断
- **`get_images(full=True)` 返回元组长 10**：img[0]=xref, img[1]=smask, img[2]=int(不是bbox)；显示位置用 `page.get_image_bbox(img)` 拿
- 最终：675 张图 / 47MB（file1=500 截断、file2=34、file4=8、file5=133）
- **PowerShell 测试中文编码坑**（继承上轮）：Invoke-RestMethod -Body 中文要 UTF8.GetBytes，否则 422/乱码

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

## chromadb telemetry 噪音根因与修复（2026-08-18）
- 现象：服务日志刷屏 `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`
- 根因：chromadb 0.6.3 按 posthog-python 3.x API 写（`capture(distinct_id, event, props)`），本机却装了 posthog 7.38.0（`capture(event, **kwargs)`），多传 3 个位置参数报错
- chroma_store.py 早已在 `_get_collection` 传 `settings=ccfg.Settings(anonymized_telemetry=False)`，但 telemetry 在 System.start() 阶段仍会触发，且 posthog 7.x 的 disabled 标志不生效
- 最终修复（config.py 顶部 load_dotenv 后）：`os.environ.setdefault("ANONYMIZED_TELEMETRY","False")` + 静默 `logging.getLogger("chromadb.telemetry").setLevel(CRITICAL)`（含 product.posthog 子 logger）
- 关键：环境变量名是 `ANONYMIZED_TELEMETRY`（pydantic BaseSettings 字段转大写），但 chromadb 的 Settings() 作为默认参数在模块 import 时求值，必须最早设置才稳；静默 logger 是双保险
- 教训：chromadb 0.6.3 + posthog 7.x 是已知不兼容，遥测纯噪音无用，静默 logger 比降级依赖更干净

## 接口响应字段约定（重要，避免重复踩坑）
- `/api/documents` 返回 `{"status":"ok","data":[...]}`（字段是 `data` 不是 `files`）
- `/api/search` 返回 `{"status":"ok","data":[...]}`（字段是 `data` 不是 `results`）
- `/api/entities/graph` 返回 `{"status":"ok","data":{"nodes":[...],"edges":[...]}}`（nodes/edges 在 `data` 内）
- `/api/plugins` 返回 `{"status":"ok","data":[...]}`
- 登录返回 `{"access_token":...}`；JWT 生成：`jwt.encode(payload, JWT_SECRET, algorithm='HS256')`，payload 含 sub/user_id/role/exp/iat
- 写回归脚本时先打印 status + text 前几百字符确认结构，别凭记忆猜字段名

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

## 第十四轮：整体体检 + Chroma 孤儿向量修复（2026-08-18）

### 体检发现 + 修复：Chroma 孤儿向量（实质问题）
- 体检发现 Chroma 向量 3186 条 vs SQLite chunks 1644 条，**1542 个孤儿向量**
- 根因：历史 delete_file 尚未接 Chroma 清理时删除的文件、或反复重建/重传残留，Chroma 遗留已删 chunk 的向量，污染检索（召回旧 chunk 显示空文件名）
- 当前 delete_file 已正确删 Chroma 向量（有 delete(cid)循环），孤儿是历史遗留
- 新增 scripts/cleanup_chroma_orphans.py：对比 Chroma id vs SQLite chunks id，只删孤儿，支持 --dry-run，分批删
- 已执行：3186 -> 1644，与 SQLite 完全一致，ensure_synced 补录 0

### 体检结论（其余均健康）
- 功能全通过：认证(200/错密码401)、文档列表4/详情/404、实体图谱(502节点/4266边)、文档图谱、检索(中文5条/乱码0条)、对话(正常4源/乱码0源)、插件(2个)、参数校验(空query422/top_k越界422)、未登录401
- 日志 ERROR 仅 chromadb posthog 遥测报错（capture() takes 1 positional argument but 3 were given，chromadb 0.6.3 已知无害 bug），无业务 Traceback/database is locked
- 数据库锁问题已彻底解决（前一轮 busy_timeout 修复生效，日志零 locked）

### ⚠️ 持续关注项
- **C 盘仅剩 9.19GB**（历史记录过 C 盘易满，曾有降到 0 字节的教训），需定期清理 EasyClaw Temp 的 deploy-verify 残留
- 服务进程内存 1859MB（本地 bge-large 模型常驻 ~1.2GB + Chroma 索引），属正常
- chromadb posthog 遥测报错刷屏（无害，可后续通过环境变量 ANONYMIZED_TELEMETRY=False 关闭）

### admin 账号
- admin / admin123；另有 user 账号（密码未探明，不需）

## 第十六轮：Obsidian/WorkBuddy 调研 + 图谱 Canvas 化 + 文件反向链接（2026-08-19）

### 真实调研结论（GitHub API 抓源码，非凭印象）
- Obsidian/WorkBuddy 主程序均 Electron 闭源（app.asar），无法读源码
- 图谱技术栈（社区开源插件复现）：渲染层用 Canvas/WebGL（**非 SVG**）；力导向用 d3-force，但**逐节点居中用 forceX/forceY，不用 forceCenter**（后者把节点吸成中心一团）
- 「节点怎么出现发展」= Timeline 时间轴（按月直方图 + 播放），参考 n23eos/advanced_graph_view
- 关键开源参考（MIT）：advanced_graph_view（最全：Timeline/PageRank/Louvain/Web Worker）、graph-plus（2D Canvas+3D WebGL）、fast-graph（Three.js GPU instancing）
- 调研报告：docs/Obsidian图谱与文件管理调研报告.md

### 已落地
1. **图谱 Canvas 化**：GraphView.vue 从 SVG → Canvas 2D + forceCenter→forceX/forceY（解决聚团+性能）
   - Canvas 手动实现 hit-test（hover/点击/拖拽节点）、zoom/pan、缩放渐显标签
   - 保留：柔和低饱和配色（Nord 风 TYPE_COLORS）、节点小巧统一（对数半径 4~11px）、隐藏 cooccur 噪声边（hideCooccur 默认 true）、图例折叠
2. **文件反向链接**：db.py 新增 get_file_backlinks（incoming/outgoing 双向），api.py 新增 GET /api/documents/{id}/backlinks；DocumentDetail.vue 加「关联文件」面板（相似度百分比+点击跳转）

### 关键事实
- links 表：source_id/target_id/link_type(similar)/weight/context，指向文件 id，是文件↔文件引用关系数据基戏
- 实体图数据：502 节点/4266 边，其中 cooccur 4225（噪声）、spec 12、compatible_process 29
- 实体节点字段：id/name/type/attributes/degree；边字段：source_id/target_id/rel_type/weight；无时间字段暴露（entities.created_at 在 DB 存在但 API 未返回）
- 4 份文件都是 2026-08-17 同一小时批量入库，时间跨度太短，Timeline 演进图暂无「先后」可看（机制可先建）
- 重启方式：Stop-Process 杀 8099 的 python 进程，再 Start-Process python server.py（无 restart 脚本）
- PowerShell 读 UTF-8 文件会乱码（GBK 默认），用 read 工具/edit 工具读到的是正确中文（之前误判源码乱码）

### 待做（用户确认「图谱+文件管理一起做」）
- 入度/出度拆分（entity_relations source_id/target_id 天然有向，当前只给总 degree）
- 局部图谱（Local Graph，N 跳邻域）
- Timeline 时间轴（需后端暴露 entities.created_at）
- 文件管理：目录树+标签+筛选统一体验（当前已有分类侧栏+型号/材料/日期筛选+卡片/表格切换）

## 第十五轮：五项反馈改进（2026-08-18，第一批）
用户提出 5 条需求，先出方案文档 `docs/五项改进整体方案.md`，然后依次开工。

### 已落地（第一批）
1. **语言归一化**（#2）：新增 `src/pipeline/language_filter.py`，高置信度繁转简（只转一对一无歧义映射，歧义字如乾/髮/後不转）+ 去日文/韩文/乱码 + 非中文长块丢弃。接入 `chunker.chunk_text` 末尾（两路入库统一生效）。flag `RAG_LANG_FILTER=1`。用户要求「不100%确定就不转」已落实。
2. **三模式对话**（#1）：新增 `src/chat/router.py`（规则意图分类 chat/knowledge/web，9/9准确）+ `generate_chat`（闲聊）+ `generate_web`（联网）+ `src/chat/web_search.py`（Tavily）。api_chat 加 mode 参数。ChatView 加模式切换（自动/知识库/闲聊/联网）+ 多轮历史。
3. **联网搜索**（#1 含，意外提前完成）：Tavily key 已通过进程环境变量存在（`tvly-dev-...`，easyclaw 环境注入，非 .env），直接实现 web 模式，端到端验证通过。
4. **插件调用面板**（#4）：发现已完整实现（PluginsView 有 SchemaForm 调用面板 + store.invoke + api.invoke + 后端 /invoke），端到端验证通过（example-plugin echo/add 正常）。

### 关键事实/坑
- **TAVILY_API_KEY 敏感**：值是 `tvly-dev-tTxRQ-...`（在进程环境变量，非 .env），config 用 os.getenv 读到。切勿外泄。
- 三模式已验证：闲聊（自我介绍）、RAG（镀金层，带引用）、web（北京天气，带[2][3][4]来源标注）。
- Tavily 搜索接口：POST https://api.tavily.com/search，body {api_key, query, max_results, search_depth}。

### 待办（第二、三批）
- 图谱布局优化 + hover 聚焦（#3）
- 界面质感升级（#5，参考 easyclaw/mimo/obsidian/workbuddy）
- MCP 市场接入（#4，阶段3，用户确认先面板后市场）

## 第二批：图谱交互优化已落地（2026-08-19）
- GraphView.vue：力导向参数调优（斥力 -300/-260、边距 70/90、cooccur 边淡到 0.10 透明白、节点尺寸 sqrt 平滑）+ forceX/forceY 平衡居中
- hover 高亮邻接（悬停节点高亮它+直接邻居，其余淡出）
- 标签默认隐藏（Obsidian 风格）、hover 显示，顶部「显示标签」开关
- 构建验证通过

## 第三批：MCP 市场已落地（2026-08-19）
- 后端已有（之前埋的）：src/mcp/ 四文件（client/manager/smithery/__init__）+ src/api_mcp.py 六端点（market/installed/install/uninstall/tools/call）
- 前端新增：frontend/src/views/McpMarket.vue（市场浏览/已安装/工具调用三区）+ frontend/src/api/mcp.js
- router 新增 /mcp（管理员专属，与 /plugins 同拦截）；MainLayout 侧边栏加「MCP 市场」入口（Shop 图标）
- 后端 api_mcp 注册方式：src/api.py 末尾 api_mcp.register(router)（与 api_plugins 同，已确认已挂载，之前误判「未注册」）

### MCP 市场关键坑（本轮踩）
1. **qualifiedName 含斜杠导致 404**：@scope/name 型 qualifiedName 在路径参数里被 / 切分。修复：api_mcp 的 tools/call 两路由改 `{qualified_name:path}` 转换器
2. **uninstall 端点误用 McpInstallReq**（要求 command 必填）→ 新增 McpUninstallReq（只 qualifiedName），否则卸载报 422
3. **CONNECT_TIMEOUT 20s 不够**：npx 首次冷启动要下载包，超时报 `unhandled errors in a TaskGroup`。已提至 60s；且验证时需先 npx 预热缓存
4. **scripts/mini_mcp_server.py 非标准 MCP**：是纯换行 JSON-RPC，缺 Content-Length 帧头，不能作 mcp 库 stdio_client 的测试目标。真实测试用 npx @modelcontextprotocol/server-everything（echo 工具）
5. **依赖**：mcp python 包已 pip 安装（import 无 __version__ 属性但 ClientSession/StdioServerParameters/stdio_client 可用）；httpx 0.28.1 已有
6. **Smithery registry**：https://registry.smithery.ai/servers，返回 servers[] 数组，字段 qualifiedName/displayName/description/verified/useCount/homepage，正常返回约 10 个 server

### MCP 端到端验证通过（2026-08-19）
- market 200 返回 10 server；installed 空；未登录 401
- 安装 npx server-everything → tools 列 13 个（echo/get-env 等）→ call echo('端到端测试') 返回 'Echo: 端到端测试'
- 卸载验证通过，清理后 installed 归空

## 十五轮续：#5 界面质感升级已落地（2026-08-19）
- 设计系统 token 早已是 v2（参考 easyclaw/mimo/obsidian/workbuddy），所以 #5 重点在「逐页落实」而非重建 token
- DocumentsView 重构：内联 style 抽成 scoped class；左侧分类栏/顶部工具栏/筛选栏/列表区结构化布局；加搜索图标、空状态副文案
- GraphView：graph-tabs 补背景 + flex-wrap；统一 --border（原误用 --border-color 导致边框不生效）
- App.vue：router-view 加 page-fade 淡入过渡（页面切换不平跳）
- global.css：补 --bg-elevated(#fff)/--bg-subtle(#f5f6f8) token（McpMarket 用了但没定义）；el-dialog 加圆角+header 分隔线；el-popper/select-dropdown 圆角统一；空状态 icon 半透明
- 六页现状评估：ChatView/PluginsView/McpMarket 已精细；DocumentsView（本轮重构）+ GraphView（工具栏）

### 五项改进全部完成（2026-08-19）
- #1 三模式对话（含联网）✅ #2 清洗归一化 ✅ #3 图谱优化 ✅ #4 插件面板+MCP市场 ✅ #5 界面质感 ✅
- 下步：综合性冒烟测试（scripts/smoke_test.py 已备好）


## DMS 作为原始文件唯一存储（2026-09-03，浏览器上传直接写 SeedDMS）

用户需求：DMS（SeedDMS）作为原始文件唯一存储（Source of Truth），浏览器上传的文件直接写入 DMS，伏羲只保存向量化索引，不再在本地 data/uploads/ 存原件副本。用户拍板「直接写 SeedDMS 数据库」路线（SeedDMS REST API 只读，无创建文档能力）+「前端选文件夹」+「旧文件先不管，链路通了重新上传」。

### SeedDMS 部署关键事实（重要，Docker + SQLite）
- SeedDMS 6.0.41 跑在 Docker 容器 `seeddms`（镜像 usteinm/seeddms:latest），端口映射 8080->80
- **数据库是 SQLite**（非 MySQL）：容器内 `/var/lib/seeddms/data/content.db`，bind mount 到宿主机 `E:\测试项目\SeedDMS\data\content.db`
- 内容目录：`contentDir=/var/lib/seeddms/data/` + `contentOffsetDir=1048576` -> 文件在 `E:\测试项目\SeedDMS\data\1048576\<docId>\<version>.<ext>`（宿主路径）
- 配置 `E:\测试项目\SeedDMS\conf\settings.xml`；完整部署说明见 `E:\测试项目\SeedDMS\配置信息`
- 账号：admin / admin（**不是 admin123**，之前 .env 里 admin123 是错的）；另一账号 guest
- Docker 容器命令：`docker exec seeddms sh -c "..."` 可读源码；源码在 `/home/www-data/seeddms60x/vendor/seeddms/core/Core/inc.ClassDocument.php` 等
- 镜像 tar：`E:\测试项目\SeedDMS\seeddms-6.0.41-image.tar`

### 直接写库的核心契约（SeedDMS 源码核对）
创建文档需写 4 张表 + 落盘文件，顺序严格：
1. `tblDocuments`：name=去扩展名、folderList=`:1:2:`（祖先 id 串）、inheritAccess=1、defaultAccess=M_READ=2、locked=-1、
   date=unix 时间戳整数
2. 文件落盘 `1048576/<docId>/<version>.<ext>`（version 从 1 起）
3. `tblDocumentContent`：dir=`"<docId>/"`、orgFileName=完整文件名、fileType=`.ext`（带点）、checksum=**md5 32位hex小写**、
   mimeType 用 mimetypes 推断
4. `tblDocumentStatus`(documentID+version) + `tblDocumentStatusLog`(status=S_RELEASED=2)

### ⚠️ 关键坑：文件名重复扩展名
- SeedDMS REST 下载 `document/{id}/content` 的 filename 是 `$document->getName() . $lc->getFileType()` **拼接**（源码 restapi/index.php 1291 行）
- 若 tblDocuments.name 存了「含扩展名」的完整文件名，下载文件名会变成 `x.txt.txt`（重复扩展名）
- 现状：连 SeedDMS 原生 UI 上传的 id=2 文档也有此问题（`.xlsx.xlsx`），属 SeedDMS 自身行为
- **修复（writer.py）**：name 存 `Path(filename).stem`（去扩展名），orgFileName 存完整文件名，fileType 存 `.ext`

### 已落地实现
- `config.py`：新增 SEEDDMS_DB_PATH / SEEDDMS_CONTENT_DIR / SEEDDMS_CONTENT_OFFSET
- `src/dms/writer.py`（新）：直接写 SeedDMS SQLite + 文件系统。核心函数 `upload_document(folder_id, filename, content) -> doc_id`、
  `list_folders()`、`document_exists()`、`get_dms_conn()`、`create_document()`；常量 M_READ=2 / S_RELEASED=2
- `src/api/dms.py`：新增 `GET /api/dms/folders`（纯文件夹列表，供上传选目录）
- `src/api/documents.py`：改造 `api_upload`——接收字节流 -> writer.upload_document 写 DMS -> 复用
  import_service.import_documents(doc_ids=[dms_doc_id]) 拉取向量化（自动记映射）
- 前端 DocumentsView.vue：上传前弹「选择 DMS 目标文件夹」对话框（el-select 展示 /api/dms/folders，按 folderList 层级缩进）
- `frontend/src/api/dms.js`：新增 folders()
- `.env`：SEEDDMS_URL 从错误的 `admin` 修正为 `http://localhost:8080`、PASS 从 admin123 改 admin、新增 DB/CONTENT 路径

### ⚠️ 关键坑（本轮踩）
1. **FastAPI multipart 的 `folder_id` 必须声明 `Form(None)`**：裸 `folder_id: int = None` 会被当 query 参数（URL 上），
   multipart form 里的 folder_id 字段被忽略，导致文件总落到根目录 folder=1 而非用户选的 folder=2。已改 `folder_id: int = Form(None)`
2. **`_stage_parse` 有 `len(text.strip()) < 50` 就抛"文档无有效内容"**：短测试文本（<50字）会触发 parse 失败 -> ctx 无 chunks ->
   embed 阶段 `KeyError: 'chunks'`。测试上传必须用 >50 字内容，真实文档（docx/xlsx/pdf）不受影响
3. **SeedDMS REST API 是只读的**：只有 login/download/search/folder-tree/version，无 create/upload。直接写库是唯一可行的「上传进 DMS」路线
4. **登录凭据 admin/admin**（非 admin123）；直接 python -c 读 .env 时 SEEDDMS_URL 曾错误地读到 `admin`（.env 历史脏值），需环境变量覆盖或改 .env

### 验证结果
- 端到端全通：上传 -> 写 DMS(doc_id) -> import_service 拉取向量化 -> 返回 file_id；SeedDMS REST 能列出+下载新文档
- 文件名修复后下载 Content-Disposition 正确（`伏羲上传测试.txt` 不再 `.txt.txt`）
- folder_id 修复后 folder=2 / folderList=:1:2: 正确
- 测试数据（doc 4/5/6/7/8 + file 68/69/70/71）已全部清理，DMS 仅剩原有 id=2 文档

### 待办（用户选择留待后续）
- 旧文件迁移：data/uploads/ 里 60+ 历史文件迁移到 DMS 后清理（用户说「链路通了重新上传」，暂不做自动迁移）
- `import_documents` 对单文档是同步阻塞（_wait_for_file 120s），大文件上传会使 HTTP 请求长时间阻塞，可考虑后台化
- SeedDMS 的 lucene 全文索引不会随写库更新（SeedDMS UI 内全文搜可能查不到新文档，属已知局限，伏羲本身检索不依赖它）
