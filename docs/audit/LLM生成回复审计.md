# 伏羲 RAG 系统 —— LLM 生成 / 回复质量链路审计报告

> 审计范围：LLM 调用封装 → 意图路由 → 编排 → 检索生成 → 流式输出 → 语义缓存 → 审计打点
> 审计方法：逐文件精读，缺陷按【严重程度】【根因】【影响】【借鉴建议】标注，定位到行号。
> 说明：本报告只做审计，不改任何代码。

---

## 一、审计文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/llm.py` | 251 | 统一 LLM 调用（async / sync / stream）+ 降级链 + JSON 提取 |
| `src/chat/router.py` | 370 | 意图路由、查询改写、复杂度路由 |
| `src/chat/engine.py` | 207 | 上下文拼接 + prompt + 引用标注 + 流式 |
| `src/chat/orchestrator.py` | 283 | 对话编排（缓存、改写、检索、生成、持久化） |
| `src/chat/cache.py` | 189 | 语义缓存（向量相似 + LRU + 版本失效） |
| `src/api/chat.py` | 140 | 非流式 / SSE 流式路由 |
| `src/chat/web_search.py` | 60 | Tavily 联网搜索 |
| `src/llm_audit.py` | 94 | LLM 调用审计（成本/质量可观测） |

---

## 二、缺陷清单

### 缺陷 1：reasoning 模型 `reasoning_content` 未被显式提取，仅靠 `finish_reason == "length"` 兜底

- **严重程度**：高
- **根因**：
  - `src/llm.py:70-78`（async）与 `src/llm.py:166-176`（sync），只读 `msg.get("content")`，完全没有读 `msg.get("reasoning_content")`。
  - 对 MiMo 这类 reasoning 模型，输出分为 `reasoning_content`（思维链）+ `content`（正式回答）。当前只对「`content` 为空 **且** `finish_reason == "length"`」时才用 `max_tokens*4` 重试（`llm.py:76-88` / `llm.py:172-184`）。
- **影响**：
  1. 若 MiMo 在 `max_tokens` 预算内完成了 reasoning，但 `content` 因某种原因（服务端行为差异、temperature）返回空而 `finish_reason` 不是 `length`（例如 `stop`），则不会触发重试，直接空 content 降级下一个 provider，**白白浪费一次 MiMo 调用**。
  2. 推理模型消耗大量 token 在 reasoning 上，`max_tokens=1024`（`engine.py:107` generate 默认）几乎必然在 reasoning 阶段耗尽，content 被截空，**每次 MiMo 都要走一次「截空 → 重试」的 double call**，延迟翻倍、成本翻倍。
  3. `reasoning_content` 被直接丢弃，无法沉淀也无可观测性（`llm_audit` 未记录 reasoning 长度）。
- **借鉴建议**：
  - 显式读取 `reasoning_content`，若 `content` 为空但 `reasoning_content` 非空，视为「推理完成但正式回答被截断」，进入加大 `max_tokens` 的重试分支，而不是只依赖 `finish_reason == "length"`。
  - 对 reasoning 模型单独约定更大的 `max_tokens` 基准（如 reasoning 模型默认 4096，非 reasoning 默认 1024），避免「每次必被截空」的稳态 double call。当前 `_build_provider_chain`（`llm.py:50-53`）hardcode 了 `mimo` timeout=60，却未 hardcode mimo 的 `max_tokens` 预算。
  - 将 `finish_reason` 与 `reasoning_content` 长度纳入 `llm_audit` 打点，量化 reasoning 占比。

---

### 缺陷 2：截空重试仅在「同一 provider 内」重试一次，无指数退避、无熔断，且重试预算固定 `max_tokens*4`

- **严重程度**：中
- **根因**：
  - `src/llm.py:76-88`（async）/ `172-184`（sync）：重试请求无任何退避（`asyncio.sleep` / `time.sleep`），失败后立刻重发，容易在服务端限流窗口内再次 429 触发降级。
  - 重试预算硬编码 `max(max_tokens * 4, 2048)`，对 `max_tokens` 本身就很小时（如分类用的 1024）是 2048；但若未来某链路 `max_tokens` 很大，`*4` 会放大到不必要的程度，一次性把成本放大 4 倍且无上限。
  - 重试只有一层（一次），若 2048 仍被 reasoning 截空，则直接空 content 走降级，无二次更长预算。
- **影响**：限流/抖动场景下降级率虚高；成本在 reasoning 模型上被 4 倍放大；缺少熔断会导致对「已确定不健康」的 provider 反复重试。
- **借鉴建议**：
  - 重试引入固定/指数退避（jitter），并对 429/5xx 分类处理（429 尊重 `Retry-After`，5xx 才立即降级）。
  - 重试预算设上限（如 `min(max_tokens*4, 8192)`），而非无界 `*4`。
  - 增加 provider 级熔断器（连续 N 次失败 → 短时跳过该 provider），避免反复撞击已故障节点。

---

### 缺陷 3：`call_llm_stream` 固定 DeepSeek Flash，无 fallback，且 reasoning 截空逻辑完全缺失

- **严重程度**：高
- **根因**：
  - `src/llm.py:124-160` `call_llm_stream`：`use_model = model or DEEPSEEK_FLASH_MODEL`（`llm.py:127`），base/key 固定 `DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY`（`llm.py:125-126`），**没有任何降级链**。
  - 流式解析只取 `delta.get("content")`（`llm.py:152`），完全忽略 `delta.reasoning_content`，且没有「content 为空 + finish_reason=length」的处理。
  - 被 `engine.py` 的 `generate_stream`（`engine.py:197-207`）调用时固定 `max_tokens=1024`，对 reasoning 模型同样会截空。
- **影响**：
  1. SSE 流式链路（`/api/chat/stream`）一旦 DeepSeek Flash 失败或返回空，**整个流直接断掉**，用户看到空白/中断，而非降级到 Pro/MiMo。这是非流式链路已经解决、但流式链路缺位的**能力不对等**。
  2. 若未来 `model=None` 处传入 MiMo，reasoning 会吞满预算，流式 content 极可能为空。
- **借鉴建议**：
  - `call_llm_stream` 复刻 `call_llm` 的降级链（遍历 provider 直到成功 yield 第一个有效 token），并在流式解析中过滤/跳过 reasoning 类型的 delta（或单独处理）。
  - 流式同样补「空 content 检测」：若整个流结束未产出任何 token，则回退到下一个 provider 重新流式生成。
  - 将流式与非流式统一到同一份「provider 链 + 空 content 兜底」逻辑，消除两套实现漂移。

---

### 缺陷 4：`__SOURCES__` 断行 bug 的根因未根治（token 级拼 JSON 仍存在断行/注入风险）

- **严重程度**：高（历史已出过线上 bug）
- **根因**：
  - `src/chat/engine.py:204-207`：
    ```python
    async for token in call_llm_stream(...):
        yield token
    yield "__SOURCES__" + _json.dumps(refs, ensure_ascii=False)
    ```
  - 历史 bug 的根因是：`__SOURCES__` 与 JSON 之间/内部依赖「前端按某个分隔符切分」，而 `refs` 里的 `content`/`file_name` 可能包含换行或 `__SOURCES__` 这类特殊串（文件名/正文里出现 `__SOURCES__` 或 `[DONE]`），导致前端切分错位、断行。
  - `api/chat.py:137-141` 的 `event_stream` 用 `f"data: {token}\n\n"` 逐 token fmt，**若某个 token 内本身含 `\n`，会破坏 SSE 事件边界**；且 `api/chat.py` 对非流式 chat 直接 `yield f"data: {answer}\n\n"`（`api/chat.py:77-80`），`answer` 里的多行内容未做 SSE 转义。
- **影响**：
  1. 文件名/正文含换行或特殊分隔串时，前端解析 `__SOURCES__` 仍会错位（根因未消除，只是当前数据恰好没触发）。
  2. SSE 事件未按 `data:` 行内转义（多行应拆成多条 `data:` 或整体 JSON 编码），含换行的 answer 会生成非法 SSE 帧。
- **借鉴建议**：
  - 不要用「魔法字符串 + 裸 JSON 内联」传递结构化来源，改用**带 type 字段的 SSE 事件协议**：`data: {"type":"token","text":...}` / `{"type":"sources","sources":...}` / `{"type":"done"}`，每帧 JSON 编码后单行发送，从根本上消除分隔串冲突与换行破坏。
  - 至少：对每个 `token`/`answer` 做 SSE 安全转义（换行拆多条 `data:` 行，或以 JSON 字符串封装），并在 `__SOURCES__` 之前可选 flush 一个明显结束标记（如 `type: done`）后再发 sources 事件。

---

### 缺陷 5：引用脚注由 LLM 生成，忠实度校验「只告警不修正」，前端仍可能收到杜撰编号

- **严重程度**：中
- **根因**：
  - `engine.py` 的 `check_citation_fidelity`（`engine.py:176-196`）能检出 `phantoms`（杜撰编号）但 orchestrator 仅 `logger.warning`（`orchestrator.py:262-268`），**不阻断、不修正、不回退**，最终 `answer` 原样返回前端。
  - `build_citation_sources`（`engine.py:154-173`）在 answer 无引用时返回**全部 refs** 作为兜底（`engine.py:162-165`）——这会把「其实没被引用」的来源也塞给前端，制造「有据可查」的假象，与「引用准确性」目标相悖。
  - prompt 里要求 `[编号]`（SYSTEM_PROMPT 第 4 条，`engine.py:28-29`），但 LLM 仍可能输出超出 refs 数量的编号（如 refs 只有 5 条，LLM 写 `[6]`）。
- **影响**：
  1. 用户点击 `[6]` 脚注得到空/错来源，损害可信度（RAG 核心价值）。
  2. 无引用时兜底返回全部 refs，前端把一个「LLM 自由发挥」的回答渲染成「有 5 个来源支撑」，误导用户。
  3. 幻影引用未被自动重写为有效引用，白白浪费已检出的情报。
- **借鉴建议**：
  - 在 `build_citation_sources` 中，对无引用的 answer **不返回全部 refs**，改为返回空序列或仅返回 `check_citation_fidelity` 认定安全的部分（可选为「无标注来源」）。
  - 对检出的 phantom 编号，后端可做一次轻量「引用清洗」：将越界编号剔除，或将 `[编号]` 映射回最近的有效 ref；或将引用不健康的结果标记 `citation_quality` 字段透传给前端置灰提示。
  - 考虑在 `generate` 后加一层「引用闭合校验」，若不健康且成本允许，回退生成（带更强的引用约束 prompt）一次。

---

### 缺陷 6：语义缓存以「原 query」键值存储，但检索/生成用的是「融合/改写后的 query」，命中语义不匹配

- **严重程度**：中
- **根因**：
  - `orchestrator.py:216-226`：缓存用 `encode_query(query)`（原始 query）查/存，key 是**原 query 的语义**。
  - 但实际检索用的是 `search_query`（可能经 `_resolve_multiturn` 融合 + `rewrite_query` 改写，`orchestrator.py:239-249`），即**同一个原 query，在不同多轮语境/改写策略下会得到不同答案**。
  - 缓存只按原 query 语义匹配，忽略了 history 语境：`「它」的规格`（多轮）与独立的`「它」的规格` 会被当成同一语义缓存命中，返回错误答案。
- **影响**：
  - 多轮指代查询（依赖 history 融合）命中缓存时，返回的是别人/别的语境下的答案，造成**上下文泄露式错误回答**。
  - 改写策略变更（`SEMANTIC_CACHE_VERSION` 需手动 bump，`cache.py:23` 注释也承认）若没同步 bump，旧答案持续被命中。
- **借鉴建议**：
  - 缓存 key 应基于**最终实际送入检索的 search_query**（融合/改写后的），而非原始 query。
  - 对依赖多轮语境的查询（`_resolve_multiturn` 发生了融合）应**跳过缓存**（缓存不适用于有态多轮）。
  - 将 cache version 与「生成 prompt 哈希 + 检索配置哈希」自动关联，检索/生成逻辑变更时自动失效，避免依赖人工 bump `SEMANTIC_CACHE_VERSION`。

---

### 缺陷 7：缓存相似度命中后 `sources` 原样复用，但来源指向的 chunk 可能已过期/删除

- **严重程度**：中
- **根因**：
  - `cache.py:133-138`（lookup）直接返回 `best_entry["sources"]`，`store`（`cache.py:167-183`）持久化时 `json.dumps(sources)`，未校验 sources 里的 `file_id`/`chunk_id` 是否仍存在。
  - `SEMANTIC_CACHE_VERSION`（`cache.py:23`）只覆盖「逻辑变更」，不覆盖「文档被重传/删除」这类数据级变更。
- **影响**：文档更新或删除后，缓存命中仍把答案连同旧 chunk 引用返回，用户点击来源指向已不存在的文档/段落。
- **借鉴建议**：
  - 缓存 `sources` 前记录文档/语料的版本或 `updated_at`，命中时校验来源有效性（或对来源做一次轻量存在性检查）。
  - 入库（ingest）触发文档变更时，主动失效相关语义缓存 key（按文档 id 关联），而非全局依赖 version bump。

---

### 缺陷 8：`np.frombuffer` 依赖「字节布局/维度一致」，无维度校验，embedding 模型升级会导致静默错配

- **严重程度**：中
- **根因**：
  - `cache.py:130`（lookup）与 `cache.py:156`（store）用 `np.frombuffer(query_embedding, dtype=np.float32)`，`_unpack`/`_pack`（`cache.py:67-75`）按 `len/4` 反推维度。
  - 若 embedding 模型/维度升级（如 768→1024），新 query 向量与旧缓存条目维度不一致，`np.dot` 会直接抛异常或（更糟）若 fortuitously 长度对齐则产出错误相似度。
  - `load_cache`（`cache.py:91-97`）对旧条目做了归一化，但**没有维度一致性校验**，不匹配维度的旧条目会混入 `_entries`。
- **影响**：embedding 模型切换后，缓存静默失效或崩错；`SEMANTIC_CACHE_VERSION` 虽有手动 bump 约定，但无强校验兜底。
- **借鉴建议**：
  - 缓存条目持久化时同时存 `dim`，`load_cache`/`lookup` 时校验维度一致，不一致直接跳过该条目或整体失效。
  - `encode_query` 返回结构改为带维度元信息，避免「裸 bytes 无元数据」的脆弱契约。

---

### 缺陷 9：`rewrite_query` / LLM 快判的错误处理吞异常，静默降级，缺少可观测性埋点

- **严重程度**：低
- **根因**：
  - `router.py:107-114`（`classify_intent_async`）与 `router.py:338-344`（`rewrite_query`）的 `except Exception` 仅 `logger.debug`，无任何审计计数。
  - `llm_audit` 有 `record_failure`/`record_retry`，但分类/改写走的是 `_call_llm_with_fallback`（`engine.py:110-119`），其内部的降级已由 `llm.py` 记录；然而「快判超时（`asyncio.wait_for` timeout=8.0）」「改写跑偏放弃」这类**业务级降级**没有进入审计统计。
- **影响**：意图分类/改写的系统性失败（如重写持续跑偏、快判持续超时）无法被 health 端点发现，运维盲区。
- **借鉴建议**：
  - 为 `classify_intent_async` 的「规则命中/LLM 纠偏/超时回退」三类结局分别打点计数，`rewrite_query` 的「规则命中/LLM 改写/跑偏回退/异常回退」同理。
  - 将 `degradation_rate()` 扩展覆盖「快判超时率」「改写跑偏率」，纳入 `/api/health` 体温计。

---

### 缺陷 10：`_resolve_multiturn` 只取「最近一条 user 消息」，且指代词拼接逻辑粗糙，易产出畸形融合 query

- **严重程度**：低
- **根因**：
  - `orchestrator.py:76-90`：`prev_user` 只取 `reversed(history)` 中**第一条** user，未考虑历史里最近的 user 是真的上一问还是历史污染。
  - `orchestrator.py:90-110`：指代词开头用 `tail = tail[len(r):].lstrip("的")` 截断，像 `「它的」+「参数」` → `"它的参数"` 会先匹配 `"它"` 截掉「它的」→ `"参数"`，再拼 prev，但 `"那个的"` 这类组合在 `_MULTITURN_REF` 里顺序靠后，`"它"` 会先命中 `"那个的"` 的 `"那"`？——实际 `q.startswith(r)` 按元组顺序匹配，`"它"` 在 `"那"` 之前，`"那个"` 会先被 `"那"` 截成 `"个"`，产生 `"prev 个..."` 的错误拼接。
- **影响**：多轮融合偶尔产出语义错乱 query，导致检索跑偏；且 `_MULTITURN_REF` 的匹配顺序存在「短词抢长词」的截断 bug。
- **借鉴建议**：
  - 指代词匹配改为「最长前缀优先」或使用显式长词表（先匹配 `"那个的"`/`"它的"` 再匹配 `"那"`/`"它"`），或改用正则以词为边界。
  - 融合后的 query 可再过一次 `_query_has_exact_entity` / 重叠度校验，异常时回退原 query。

---

### 缺陷 11：非流式 `chat` 模式的 `history` 截断与持久化来源不一致

- **严重程度**：低
- **根因**：
  - `engine.py:129-134` `generate_chat` 用 `history[-6:]` 截断最近 6 条；而 `api/chat.py:20-24` 的 `_trim_history` 截断到最近 20 条，`_validate_history` 限单条 content ≤ 4000 字符。两者截止口径不一致（6 vs 20）。
  - `orchestrator.py:151-173` 持久化时 `add_conversation_message` 存 `sources=[]`（user）与 `sources=result.get("sources", [])`（assistant），但**从库读回**的历史 `get_conversation_messages` 只取 role/content，丢失了历史的 sources/mode，导致多轮里引用信息无法复用。
- **影响**：
  1. chat 模式实际只用 6 条历史，但 API 层允许 20 条，输入到 prompt 的上下文窗口比预期小，多轮连续性弱。
  2. 持久化的历史丢弃 sources，后续多轮无法引用前几轮的来源锚点（如果需要）。
- **借鉴建议**：
  - 统一历史截断口径（单常量配置），明确 chat/knowledge 各自允许的历史条数与总 token 上限。
  - 历史持久化与读取保持结构一致，至少保留 mode；若需多轮引用，则保留 assistant 的 sources。

---

### 缺陷 12：`call_llm_sync` 重试循环中 `last_err` 未传递，且「空 content 非 length」时同样被吞

- **严重程度**：低
- **根因**：
  - `src/llm.py:147-197` `call_llm_sync`：`retry` 内层循环，`content` 为空且非 `length` 时（`llm.py:184` 逻辑只在 sync 版同样仅 length 触发），落到 `logger.warning("返回空 content...重试")`，但 `record_empty_content` 与重试继续，最终 `return ""`（`llm.py:197`）**不抛异常**，调用方（入库链路）拿到空串可能当作「成功」往下走。
- **影响**：入库/后台线程里 LLM 空回复被静默当成功，可能写入空摘要/空标签，数据质量隐患；失败静默，难以定位。
- **借鉴建议**：
  - 空 content 也应走 `finish_reason` + `reasoning_content` 双判定（同缺陷 1），重试耗尽仍空时返回明确的失败信号（抛异常或返回 `(ok, result)` 元组），由调用方决定是否写库。
  - sync 版本把最后一轮的真实 `last_err` 透传出去，便于告警。

---

## 三、审计重点逐项结论

### 1. 多模型 fallback 链健壮性 —— 部分健壮，流式链路缺失
- 非流式 `call_llm` 降级链完整（Flash→Pro→MiMo），每 provider 异常都被捕获并记录（`llm.py:56-98`）。
- **缺口**：`call_llm_stream`（SSE 链路）无任何降级（缺陷 3），是最大能力不对等点。
- MiMo 超时 hardcode 60s，与 DeepSeek 的 `DEEPSEEK_TIMEOUT=60` 一致，但 MiMo 是 reasoning 模型，应有更宽裕预算。

### 2. MiMo reasoning_content 吞 max_tokens —— 处理不完善
- 只依赖 `finish_reason == "length"` + 空 content 触发一次 `*4` 重试，未显式读 `reasoning_content`（缺陷 1、2）。
- 流式链路完全无此处理（缺陷 3）。
- **结论：未完善，是当前最需要优先修的点。**

### 3. 引用脚注 + SSE 流式正确性 —— 根因未根治
- `__SOURCES__` + 裸 JSON 内联的方案本质上脆弱（缺陷 4），历史上断行 bug 的根因（分隔串冲突 + 换行破坏）仍在。
- 引用忠实度只告警不修正（缺陷 5）。

### 4. 回复标准度 —— 尚可，但有兜底误导
- prompt 质量较好（SYSTEM_PROMPT 明确「不编造、引用用 [编号]」），上下文截断有 `max_chunk_chars=800` / `max_total_chars=6000` 控制（`engine.py:47-49`）。
- **问题**：无引用时兜底返回全部 refs（`engine.py:162-165`）与「引用准确性」目标相悖（缺陷 5）。

### 5. 语义缓存命中率/失效策略 —— 需改进
- 缓存 key 用原 query，与改写/融合后的 search_query 不匹配（缺陷 6）；来源过期未校验（缺陷 7）；维度无校验（缺陷 8）。
- 命中阈值 0.92 偏高但有 LRU + version 兜底。

### 6. 并发/超时/重试 —— 支持但粗糙
- 无退避、无熔断、无 `Retry-After` 处理（缺陷 2）；超时统一 60s，reasoning 模型可能不足）；分类有独立 `wait_for(8s)` 但慢判无独立超时上限（依赖 `call_llm` 内 timeout）。

---

## 四、修复优先级建议（供后续排期参考）

| 优先级 | 缺陷 | 理由 |
|--------|------|------|
| P0 | 缺陷 3（流式无降级） | 线上 SSE 主链路直接断流，无兜底 |
| P0 | 缺陷 1（reasoning_content 未提取） | 每次 MiMo 稳定 double call，成本/延迟翻倍 |
| P0 | 缺陷 4（`__SOURCES__` 断行根因） | 历史线上 bug，根因未消除 |
| P1 | 缺陷 6（缓存 key 错配 + 多轮语境） | 上下文泄露式错误回答 |
| P1 | 缺陷 5（引用忠实度不修正/兜底误导） | 损害 RAG 可信度核心价值 |
| P1 | 缺陷 2（重试无退避无熔断） | 限流场景降级率虚高 |
| P2 | 缺陷 7 / 8（缓存来源过期 / 维度校验） | 数据级正确性 |
| P2 | 缺陷 9 / 10 / 11 / 12 | 可观测性、多轮融合、历史一致性 |

---

*报告生成时间：2026-09-08。基于 E:\更新RAG框架 当前源码静态审计，未修改任何代码。*
