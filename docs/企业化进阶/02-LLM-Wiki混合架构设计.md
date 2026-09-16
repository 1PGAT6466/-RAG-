# 02・LLM Wiki 混合架构设计（引入 Wiki 编译层）

> 目标：在现有 "原始文档 → chunk → 检索" 链路之上，加一层
>
> **知识编译层（LLM Wiki）**
>
> ，让高频、高价值知识（选型、标准、工艺、概念）沉淀为可读、可编辑、可双链、可版本化的结构化页面；查询时 
>
> **Wiki 优先、RAG 兜底**
>
> ，两者共享同一套溯源体系。
> 设计原则：
>
> **复用现有引擎 / 存储 / 图谱，不推翻任何已验证的检索逻辑；新增的东西全部是 "加表 + 加 stage + 加路由"，可回滚。**



***

## 1. 为什么在你的场景引入 Wiki 是合理的（先说清楚边界）

你的知识库特征：\~96 份工业文档（连接器设计手册、Mini-fakra 规格、标准件表、工艺规程），**数量小、更新低频、主题高度集中**—— 这正是 LLM Wiki 编译层的理想场景（Karpathy 范式适用区间），同时你又保留了大文档原文检索需求（长尾、精确条款）。

**引入后的分工：**



| 知识类型                                     | 走哪条路              | 原因                  |
| ---------------------------------------- | ----------------- | ------------------- |
| 选型（导轨 / 连接器 / 材料）、标准（GB/T、IEC）、工艺步骤、概念定义 | **Wiki 编译页**      | 高频复用、需要综合、需要人工可编辑修正 |
| 精确条款、报错原文、新上传未编译文档、长尾细节                  | **RAG 原文检索**      | 保真溯源、新鲜度、规模         |
| 复杂多跳关系（"哪些材料兼容镀金工艺"）                     | **图谱实体导航**（已有，保留） | 关系优先                |

**关键认知**：Wiki 不是替代 RAG，是给 RAG 加一层 "高质量前置答案层"。两者共同的底座是**同一个溯源体系**（source\_file\_ids → file → chunk），所以答案永远可以回到原文。



***

## 2. 数据模型（新增 3 张表，全部独立于旧表）



```
\-- 1) Wiki 页面（编译产物）

CREATE TABLE wiki\_pages (

&#x20;   id INTEGER PRIMARY KEY AUTOINCREMENT,

&#x20;   slug TEXT NOT NULL UNIQUE,              -- 稳定标识：'fakra-connector-guide'

&#x20;   title TEXT NOT NULL,                    -- 页面标题（概念名/主题名）

&#x20;   category TEXT NOT NULL DEFAULT '未分类', -- 复用 src/classification.py 分类字典

&#x20;   content\_md TEXT NOT NULL DEFAULT '',    -- Markdown 正文（编译产物，人工可编辑）

&#x20;   summary TEXT NOT NULL DEFAULT '',       -- 一句话摘要（列表页/路由用）

&#x20;   source\_file\_ids TEXT NOT NULL DEFAULT '\[]', -- 来源文档 \[file\_id]，溯源锚点

&#x20;   entity\_ids TEXT NOT NULL DEFAULT '\[]',  -- 关联实体 \[entity\_id]（复用图谱实体）

&#x20;   status TEXT NOT NULL DEFAULT 'draft',   -- draft | published | stale | archived

&#x20;   compiled\_by TEXT NOT NULL DEFAULT 'llm',-- llm | human | llm+human（人工编辑后升级）

&#x20;   version INTEGER NOT NULL DEFAULT 1,

&#x20;   created\_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),

&#x20;   updated\_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))

);

CREATE INDEX idx\_wiki\_cat ON wiki\_pages(category);

CREATE INDEX idx\_wiki\_status ON wiki\_pages(status);

\-- 2) Wiki 双链（页面间关系，干净的新表，不碰语义混乱的旧 links 表）

CREATE TABLE wiki\_links (

&#x20;   id INTEGER PRIMARY KEY AUTOINCREMENT,

&#x20;   from\_page\_id INTEGER NOT NULL REFERENCES wiki\_pages(id) ON DELETE CASCADE,

&#x20;   to\_page\_id INTEGER NOT NULL REFERENCES wiki\_pages(id) ON DELETE CASCADE,

&#x20;   link\_type TEXT NOT NULL DEFAULT 'wiki', -- wiki(双链) | related(相关主题) | source(同源文档)

&#x20;   created\_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),

&#x20;   UNIQUE(from\_page\_id, to\_page\_id, link\_type)

);

\-- 3) Wiki 版本历史（人工编辑与重编译都可追溯、可回滚）

CREATE TABLE wiki\_versions (

&#x20;   id INTEGER PRIMARY KEY AUTOINCREMENT,

&#x20;   page\_id INTEGER NOT NULL REFERENCES wiki\_pages(id) ON DELETE CASCADE,

&#x20;   version INTEGER NOT NULL,

&#x20;   content\_md TEXT NOT NULL,

&#x20;   changed\_by TEXT NOT NULL DEFAULT 'llm', -- llm | \<username>

&#x20;   note TEXT NOT NULL DEFAULT '',

&#x20;   created\_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))

);
```

**为什么这样设计：**



* `slug` 稳定 → 双链 / 引用 / 前端路由不随标题变化断裂；

* `source_file_ids` → 答案永远可溯源回原文（解决 Wiki 模式最大痛点：二手内容不可信）；

* `entity_ids` → 直接复用你已有的 `entities` 表，Wiki 页面 = 实体的 "精炼知识页"；

* `status=stale` → 原文更新时页面标记失效需重编译，防止错误固化；

* `wiki_versions` → 人工编辑覆盖 LLM 产物，且可回滚（你之前担心的 "错误固化" 由此缓解）。



***

## 3. 编译流程（复用现有入库引擎，新增一个 async Stage）

### 3.1 编译触发（三种入口，全部进 `engine.py` 的 Stage 体系）



```
入口 A：入库后自动编译 —— 新增 async stage "compile\_wiki"

&#x20;   条件：文件满足任一（doc\_kind ∈ {技术手册,规格书,标准} OR authority ≥ 4 OR 人工勾选"编译为知识页"）

入口 B：人工触发 —— 文档详情页"编译为 Wiki 页面"按钮 → 调 /api/wiki/compile {file\_id}

入口 C：批量回填 —— 对存量高价值文档跑 scripts/compile\_wiki\_backfill.py
```

### 3.2 单文件编译 Stage 实现（伪代码，落位 `src/pipeline/wiki_compiler.py`）



```
@register\_stage("compile\_wiki", stage\_type="async", critical=False)  # 失败只降级，不阻断入库

def \_stage\_compile\_wiki(ctx):

&#x20;   file\_id = ctx\["file\_id"]

&#x20;   text = ctx.get("text", "")

&#x20;   if not \_should\_compile(file\_id, text):      # 条件见 3.1

&#x20;       return

&#x20;   # 1) 判断是"新增"还是"更新"：按 source\_file\_ids 是否已含 file\_id

&#x20;   pages = get\_wiki\_pages\_by\_source(file\_id)

&#x20;   # 2) 构造编译 prompt（结构化输出，用现有 extract\_json 解析）

&#x20;   md, meta = llm\_compile\_page(text, file\_id)  # 返回 (markdown 正文, 元数据)

&#x20;   # 3) 写入/更新 wiki\_pages + 关联实体 + 双链建议 + 版本记录

&#x20;   upsert\_wiki\_page(file\_id, md, meta)

&#x20;   # 4) 幂等：同一文件重跑不产生重复页（按 slug 唯一）
```

### 3.3 编译 Prompt（结构化输出契约）



```
系统：你是工业知识库的"知识编译员"。把给定的文档资料编译成一篇结构化 Markdown 知识页。

输出 JSON：

{

&#x20; "slug": "英文/拼音稳定标识",

&#x20; "title": "页面标题（概念名）",

&#x20; "category": "从分类字典选一个",

&#x20; "summary": "一句话摘要（≤50字）",

&#x20; "content\_md": "Markdown 正文，结构：## 定义 / ## 关键参数（表格）/ ## 选型要点 / ## 相关标准 / ## 注意事项。只写资料中真实存在的内容，不编造数值",

&#x20; "entities": \["资料中出现的关键实体名（连接器型号/材料/标准号）"],

&#x20; "related\_topics": \["建议关联的其它主题名"]

}

约束：数值/型号/标准号必须原文抄录，不得改写；内容超长时优先保留参数表和结论。
```

> 用现有 
>
> `call_llm_sync`
>
> （后台线程）+ 
>
> `extract_json`
>
>  解析，失败降级跳过（Stage 非 critical）。成本估算：单页编译～2-4k token，96 份文档全量编译 < 50 万 token，Flash 模型可接受。

### 3.4 更新一致性（防错误固化）



* 原文重入库 / 替换（走 `delete_file` 级联 + 重入库）时：`wiki_pages.status → stale`，同时记录 `wiki_versions`（旧版可查）；

* 定期（或启动时）跑 `scripts/wiki_stale_check.py`：列出所有 stale 页，人工确认后触发重编译或保留人工编辑版（`compiled_by='human'` 的页**不自动覆盖**，只提示差异）；

* 人工编辑 = 最高优先级：`compiled_by` 升级为 `human` 后，LLM 重编译只生成 "建议新版本" 写入 `wiki_versions`，不直接覆盖。



***

## 4. 查询侧：Wiki 优先路由（改 `orchestrator.py`，不动 `search.py`）

### 4.1 路由判定（在 `_handle_knowledge` 里，改写 / 检索之前插入）



```
\# 伪代码：知识库查询新增 Wiki 优先层

wiki\_hits = await wiki\_router(search\_query)   # 见 4.2

if wiki\_hits and wiki\_hits\[0]\["confidence"] >= WIKI\_HIT\_THRESHOLD:

&#x20;   # Wiki 命中：以编译页为上下文回答，仍带溯源

&#x20;   answer, refs = generate\_wiki(query, wiki\_hits, rag\_results=rag\_results)

else:

&#x20;   # 未命中/置信不足：走原 RAG 链路（完全不变）

&#x20;   answer, refs = generate(query, results)
```

### 4.2 `wiki_router` 命中判定（三层，零 LLM 优先）



1. **精确标题 / 别名命中**：query 分词（复用 `_query_terms`）与 `wiki_pages.title/slug` 精确匹配 → 高置信；

2. **实体命中**：query 含的实体（复用 `entity_extractor.extract_rule`）在 `wiki_pages.entity_ids` 中 → 中置信；

3. **FTS 检索**：对 `wiki_pages.content_md` 建 FTS5 表，`fts_search` 取 top3 → 低置信。

置信度不足时：**Wiki 结果 + RAG 结果一起进上下文**（Wiki 页作为权威前置块 \[1]，RAG 原文块 \[2..n]），既快又保真。

### 4.3 对检索质量的预期收益



* 概念 / 选型 / 标准类问题：不再依赖 "碎片拼接"，直接答编译页 → **连贯性、完整性显著提升**；

* 引用溯源：Wiki 页 `source_file_ids` 映射回原文，脚注跳转原文段落仍可用；

* 冷启动：Wiki 页按需编译（只编译高价值文档），不会一次烧光预算。



***

## 5. 前端：Wiki 视图（新增 1 个视图 + 复用 2 个能力）



| 页面              | 能力                                                     | 复用                                                                  |
| --------------- | ------------------------------------------------------ | ------------------------------------------------------------------- |
| `WikiView.vue`  | 页面列表（按分类）、搜索、新建（人工写）/ 编辑（Markdown 编辑器）、版本历史查看、stale 标记 | 复用 `SchemaForm`/`SchemaResult` 的表格渲染                                |
| `WikiGraph.vue` | 双链关系图（页面节点 + 实体节点）                                     | **复用&#x20;**`GraphView`**&#x20;的 D3 图渲染**（`views/graph/helpers.js`） |
| 对话来源展示          | 答案引用 Wiki 页时，脚注显示 "知识页：xxx（来源：xxx 文档）"                 | 复用 `build_citation_sources` 结构，加 `source_type: 'wiki'`              |

路由（`router.js` 加两行）：`/wiki`（列表 + 编辑）、`/wiki/:slug`（详情 + 双链）。



***

## 6. 后端 API（新增 8 个端点，全部挂 `src/api/wiki.py`）



```
GET    /api/wiki/pages                   列表（按分类/状态筛选）

GET    /api/wiki/pages/{id}              详情（含双链、版本列表）

POST   /api/wiki/pages                   人工新建页面

PUT    /api/wiki/pages/{id}              编辑（写 wiki\_versions，compiled\_by→human）

POST   /api/wiki/pages/{id}/compile      触发重编译（stale 页）

POST   /api/wiki/compile {file\_id}       单文档编译（入口 B）

GET    /api/wiki/pages/{id}/graph        双链图数据（D3 复用）

GET    /api/wiki/stale                   待重编译清单（管理页）
```

权限：`require_admin` 管新建 / 编辑 / 编译；普通用户只读。



***

## 7. 落地步骤（3 个里程碑，可独立验收）

### M1：地基（半天～1 天）



* 建 3 张表（`wiki_pages/wiki_links/wiki_versions`）+ `init_db` 注册；

* `src/storage/wiki.py`：CRUD + 版本 + 双链 + stale 管理；

* 跑通：`scripts/compile_wiki_backfill.py` 对 3\~5 篇高价值文档编译，`sqlite3` 手工查页。

### M2：链路（1\~2 天）



* `src/pipeline/wiki_compiler.py`（Stage）+ `src/api/wiki.py`（8 端点）；

* `wiki_router` 接入 `orchestrator._handle_knowledge`（先灰度：`RAG_WIKI_ROUTE=1` 时开，默认 0）；

* 前端 `WikiView.vue`（列表 / 详情 / 编辑）上线。

### M3：闭环（1\~2 天）



* 双链图（复用 GraphView）；

* stale 检测脚本 + 重编译按钮；

* 用 02 文档的评测体系对比：Wiki 路由开启前后，概念 / 选型类问题的答案忠实度与完整性分数。

**验收标准（M3 结束时）**：



1. 高价值文档 ≥80% 已编译成 Wiki 页（可查 `wiki_pages` 计数）；

2. 概念 / 选型 / 标准类查询，Wiki 路由命中率 ≥60%，答案忠实度（LLM judge）≥4/5；

3. 所有 Wiki 答案可溯源回原文文件（`source_file_ids` 非空）；

4. 人工编辑过（`compiled_by='human'`）的页面，重编译不覆盖（版本历史可回滚）；

5. RAG 长尾 / 精确条款查询不受影响（回归 benchmark 不劣化）。



***

## 8. 风险与对策



| 风险                      | 对策                                                                        |
| ----------------------- | ------------------------------------------------------------------------- |
| LLM 编译幻觉沉淀进知识库          | ① 所有数值 / 型号 "必须原文抄录" 约束；② `source_file_ids` 溯源；③ stale + 版本机制；④ 人工编辑最高优先级 |
| 编译成本失控                  | 只编译高价值文档（条件过滤）；Flash 模型；单页 ≤2-4k token                                    |
| Wiki 命中误路由（把不相关问题引到编译页） | 三层置信判定 + 阈值；置信不足时 Wiki+RAG 混合进上下文，不二选一                                    |
| 与旧 links / 图谱表冲突        | 全新表 `wiki_*`，不碰旧 `links`；实体通过 `entity_ids` 只读关联                           |
| 引入后回归                   | `RAG_WIKI_ROUTE` 开关默认关，灰度开启，每步跑 benchmark 对比                              |