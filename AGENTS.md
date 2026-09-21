# AGENTS.md — 更新RAG框架（伏羲 RAG）

> 别名说明：项目对外名称「伏羲 RAG」（见 README），内部目录/仓库名「更新RAG框架」。
> 两者指同一系统，检索/沟通时请勿混淆。

## Quick start

```bash
# Backend (FastAPI + SQLite + ChromaDB)
python server.py                       # starts on http://127.0.0.1:8099

# Frontend dev server (Vue 3 + Element Plus + Vite)
cd frontend && npm run dev             # starts on http://localhost:3000, proxies /api → :8099

# Frontend production build (served by backend as SPA)
cd frontend && npm run build           # output → frontend/dist/, server.py mounts it

# Tests (pytest — tests/ 下有 18+ 用例文件)
python -m pytest                       # 全量单元/集成测试
python -m pytest tests/test_storage.py -q

# Smoke test (requires running server + data/rag.db with documents)
python scripts/smoke_test.py           # full chain: auth → search → chat → graph → plugins → RBAC
python scripts/smoke_test.py --base http://192.168.x.x:8099

# DB schema migration (versioned, schema_version table)
python scripts/migrate.py status       # 当前版本 + 待执行迁移
python scripts/migrate.py upgrade      # 应用 pending 迁移
```

**双轨验证**：`pytest`（组件级回归）+ `scripts/smoke_test.py`（端到端全链路）。
注意：`init_db()` 启动时会自动应用 `migrations/` 下的 pending 迁移（见 Gotchas 11）。

## Architecture

```
server.py          → FastAPI app, lifespan init, SPA fallback
config.py          → all config from .env, single source of truth for flags
src/api/           → HTTP routes split by domain, thin controller layer:
                     auth / chat / conversations / documents / search / graph /
                     config / dms / feedback / wiki / errors
src/auth/          → JWT auth (login/register) + RBAC (get_current_user / require_admin) + rate_limit
src/pipeline/      → document ingest engine (Stage-based, sync+async) + parser/chunker/backends/quality
src/retrieval/     → hybrid search: BM25 + vector + graph recall → RRF fusion → rerank
src/chat/          → LLM generation with citation markup [1][2] + semantic cache
src/storage/       → SQLite (WAL mode) + ChromaDB vectors + FTS5 full-text
                     connection.py (统一连接层) / files.py / chunks.py / entities.py /
                     conversations.py / feedback.py / wiki.py / tasks.py / db.py (re-export)
src/extraction/    → entity extraction (rule-based + optional LLM)
src/plugins/       → plugin system (manifest.json + subprocess host + hooks + registry)
src/mcp/           → Smithery MCP marketplace integration
src/dms/           → SeedDMS writer/import/sync (原件唯一存储)
src/llm/ + src/llm_client.py → LLM calls (httpx) + fallback chain
src/classification.py → single authoritative category dictionary (shared by classify + ranking)
migrations/        → versioned schema migrations (001-005) + scripts/migrate.py
frontend/          → Vue 3 SPA (Element Plus, D3 graph, Pinia stores)
plugins/           → plugin directories (each has manifest.json + entry .py)
scripts/           → operational scripts (start/stop, smoke test, data migration, watcher)
tests/             → pytest suites (18 files)
data/              → runtime data (rag.db, uploads, images, ChromaDB) — gitignored
```

## Key conventions

### Feature flags (config.py, all default ON unless noted)

All flags are string `"1"` / `"0"`, read from .env:

| Flag | Controls |
|------|----------|
| `RAG_CHROMA` | ChromaDB vector store (vs SQLite brute-force fallback) |
| `RAG_JIEBA` | jieba tokenizer for FTS5 |
| `RAG_DYNAMIC_RANKING` | Dynamic BM25/vector weight by query type |
| `RAG_RERANK` | Rerank after fusion (SiliconFlow → DeepSeek → local TF-IDF) |
| `RAG_GRAPH_RECALL` | Graph recall as 3rd retrieval source |
| `RAG_ENTITY_EXTRACT` | Rule-based entity extraction on ingest |
| `RAG_ENTITY_LLM` | LLM entity extraction (default OFF — slow, expensive) |
| `RAG_AUTO_SUMMARY/TAG/PREINDEX/SEMANTIC/DOC_SIM` | Async post-processing stages |
| `RAG_PDF_OCR` | OCR for scanned/garbled PDFs ("auto" or "force") |
| `RAG_STREAM_INGEST` | Streaming PDF ingest (large PDFs, indexed page-by-page) |
| `RAG_IMAGE_EXTRACT` | Extract embedded images for readable mode |
| `RAG_LANG_FILTER` | Traditional→Simplified Chinese normalization + CJK filtering |

### LLM fallback chain

**Chat/generate** (async): DeepSeek Flash → DeepSeek Pro → MiMo
**Structured output** (sync, in stages): configurable via `prefer_deepseek` flag
**Rerank**: SiliconFlow BGE-Reranker → DeepSeek LLM scoring → local TF-IDF+jieba
**Embedding**: local BAAI/bge-large-zh-v1.5 (default) → remote API (SiliconFlow, for large batches)

All LLM calls use `httpx`. Chat paths are async; ingest stages use sync `httpx.Client` in background threads.

### Ingest pipeline (pipeline/engine.py)

Stages run in order. Sync stages are fast (zero LLM). Async stages run in a background thread after sync completes.

```
Sync:  parse → chunk → embed (critical) → store (critical) → classify → extract
Async: summarize → tag → preindex → semantic → docsim → images
```

- `critical=True` means stage failure aborts the whole task
- Non-critical stages degrade silently (logged as warning)
- Large PDFs (≥20 pages) take the streaming path: parse page-by-page, index incrementally
- Task state is in-memory only (`_tasks` dict) — lost on restart

### Retrieval pipeline (src/retrieval/search.py)

```
query → BM25 (FTS5) + Vector (ChromaDB) + Graph recall (entity navigation)
     → dynamic-weight RRF fusion
     → exact model/standard boost + category boost
     → rerank (optional)
     → plugin on_search hook
```

- If BM25 AND graph both return 0 results, return empty immediately (vector alone cannot judge irrelevance)
- Graph recall reuses `entity_extractor.extract_rule` on the query to find navigable entities

### Storage

- **SQLite** with WAL mode + `busy_timeout=5000ms`. Thread-local connections via `threading.local()`.
- **FTS5** with `unicode61` tokenizer (jieba segmentation applied at insert time via `segment_for_fts`)
- **ChromaDB** for vector search (optional, `RAG_CHROMA=1`)
- All paths are relative to `BASE_DIR` (project root)
- DB path: `data/rag.db` (configurable via `DB_PATH` in .env)

### Auth model

- `get_current_user` — any authenticated user
- `require_admin` — admin role only (upload, delete, category/tag changes, plugin management)
- JWT secret auto-generated on first run, written back to .env
- Rate limiting on login (per IP) and registration

### Plugin system (src/plugins/)

- Plugins live in `plugins/<name>/` with `manifest.json` (must have `api_version: "1"`)
- Two kinds: `tool` (callable methods) and `workflow` (entry script)
- Hooks: `on_ingest` (after file ingest), `on_search` (after retrieval, can modify results)
- Plugins run in isolated subprocesses via `host.py` process pool
- Method whitelist enforced: only manifest-declared methods are callable

### Entity extraction (src/extraction/)

Domain-specific rule patterns for:
- **Connectors**: FAKRA, Mini-FAKRA, MLG series, HSD, etc.
- **Materials**: LCP, PA66, PBT, PPS, PEEK, copper alloys, etc.
- **Standards**: GB/T, ISO, IEC, DIN, QC/T (with normalization: strip year, merge same-number variants)
- **Processes**: welding, plating, stamping, injection molding, etc.
- **Parameters**: impedance, frequency, rated voltage/current, temperature range, etc.

Series normalization: `MLG12-45` → entity `MLG12` with `attributes.variants: ["45"]`

### Classification (src/classification.py)

Single authoritative dictionary used by BOTH document auto-classification and retrieval ranking boost. Do not create duplicate category keyword lists elsewhere.

Categories: 外购件选型, 连接器, 材料选型, 工艺规程, 机械设计, 标准件, 品质管理, 电气自动化, 操作手册, 未分类

## Gotchas

1. **FTS query injection**: `db.fts_search` interpolates the FTS query string into SQL. The `_to_fts_query` tokenizer mitigates most cases, but be careful when modifying that path.

2. **Thread-local SQLite in async context**: `_get_conn()` uses `threading.local()`. If you call DB functions from async code running on uvicorn's thread pool, connections may be shared unexpectedly. The `busy_timeout=5000` PRAGMA is the safety net.

3. **SPA fallback swallows unmatched routes**: `server.py` mounts `/{full_path:path}` for SPA. Any new API route that doesn't start with `/api/` will return 200 + HTML instead of 404.

4. **MiMo is a reasoning model**: Its `reasoning_content` can consume `max_tokens` leaving `content` empty. The codebase handles this with empty-content retry logic. When adding new LLM call sites, always check for empty content.

5. **ChromaDB telemetry crash**: `config.py` silences chromadb telemetry at startup because posthog API versions conflict. Don't re-enable it.

6. **Standard number normalization is lossy**: `GB/T 157-2001` → `GB/T 157` (year stripped). This is intentional for dedup but means you can't recover the original year from the entity.

7. **No `.env.example` checked in**: The `.gitignore` excludes `.env` but allows `.env.example`. If one doesn't exist, check `config.py` for all `_env()` calls to see the full config surface.

8. **Frontend dist is served by backend in production**: The backend mounts `frontend/dist/assets/` and has a SPA fallback. Build the frontend before testing production mode.

9. **MCP SDK dependency conflict**: `pip install mcp` (v2.0+) installs `httpx2` which **replaces** `httpx`, breaking all `import httpx` across the project. Always use `mcp>=1.0.0,<2.0.0` (pinned in `requirements.txt`). mcp 1.x uses native httpx, no conflict.

10. **Dependencies are range-pinned**: `requirements.txt` uses `>=` with upper bounds only where risk is high (mcp, chromadb, PyMuPDF, pydantic). Adding new deps: always consider an upper bound for libs known to break APIs across majors.

11. **Startup auto-migrates**: `init_db()` runs `migrations/` pending migrations and maintains `schema_version`. Do NOT add schema changes inline in `init_db()` — put them in a numbered migration file (`migrations/00N_*.py` with `up(conn)` / `UP_SQL`).

12. **Timestamps are UTC**: All `created_at`/`updated_at`/`imported_at` columns default to UTC (`datetime('now')`) since migration 005. Do not use `datetime('now','localtime')` in new SQL. API/frontend handles local display; `feedback.py`/`rerank.py` TTL parsers assume UTC.

13. **rerank_cache schema is fixed**: columns are `(q, top_k, fp, results_json, created_at)` with composite PK. It previously conflicted with a duplicate definition in `rerank.py` causing silent cache failure. Keep `connection.py` SCHEMA as the single source; `rerank.py` must not redefine it.

14. **FTS sync is manual + reconciled**: `chunks_fts`/`chunks_fts_tri` have no triggers (inserts go through `segment_for_fts` jieba). On delete, `permanent_delete_file` must delete both FTS rows AND the `chunks` rows. Startup calls `reconcile_fts()` to repair orphans.

15. **All rerank paths emit 0-1 scores**: SiliconFlow (native 0-1), DeepSeek (normalized 0-10 → 0-1), local TF-IDF (min-max blended with RRF). Keep any new scorer on the same 0-1 scale or downstream ordering will jump between providers.

## .env essentials

```env
HOST=127.0.0.1
PORT=8099
JWT_SECRET=<auto-generated, 64 chars>
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash
MIMO_API_KEY=...
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5
SILICONFLOW_API_KEY=...          # for rerank + remote embedding
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
EMBEDDING_DEVICE=cpu
DB_PATH=data/rag.db
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```
