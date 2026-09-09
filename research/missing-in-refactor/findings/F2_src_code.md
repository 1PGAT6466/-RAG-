# F2: src/ Directory File-by-File Comparison

**Original**: `E:\更新RAG框架\src`
**Refactored**: `E:\更新RAG框架\重构文件\src`

---

## 1. Files in Original NOT in Refactored (1 file)

| File | Lines | Notes |
|------|------:|-------|
| `api.py` | 549 | Monolithic API module; replaced by the new `api/` package (7 files, 455 total lines) in the refactored version |

## 2. Files in Refactored NOT in Original (14 files)

### New `api/` package (7 files — see Section 4)

| File | Lines |
|------|------:|
| `api/__init__.py` | 22 |
| `api/auth.py` | 45 |
| `api/chat.py` | 66 |
| `api/conversations.py` | 44 |
| `api/documents.py` | 145 |
| `api/graph.py` | 45 |
| `api/search.py` | 88 |

### Other new files (7 files)

| File | Lines | Notes |
|------|------:|-------|
| `chat/orchestrator.py` | 145 | New module — likely extracted from `chat/engine.py` |
| `llm.py` | 191 | New top-level LLM abstraction |
| `storage/chunks.py` | 156 | New — storage concern split from `storage/db.py` |
| `storage/conversations.py` | 89 | New — storage concern split from `storage/db.py` |
| `storage/entities.py` | 268 | New — storage concern split from `storage/db.py` |
| `storage/files.py` | 256 | New — storage concern split from `storage/db.py` |
| `storage/tasks.py` | 55 | New — storage concern split from `storage/db.py` |

## 3. Files Present in BOTH — Line-Count Comparison (flag >10-line diff)

| File | Original | Refactored | Diff | Flag |
|------|--------:|----------:|-----:|:----:|
| `__init__.py` | 1 | 1 | 0 | |
| `api_mcp.py` | 146 | 146 | 0 | |
| `api_plugins.py` | 80 | 80 | 0 | |
| `auth/__init__.py` | 4 | 4 | 0 | |
| `auth/deps.py` | 27 | 27 | 0 | |
| `auth/jwt.py` | 98 | 98 | 0 | |
| `auth/rate_limit.py` | 70 | 70 | 0 | |
| `chat/__init__.py` | 4 | 4 | 0 | |
| `chat/cache.py` | 166 | 166 | 0 | |
| **`chat/engine.py`** | **255** | **150** | **-105** | **FLAG** |
| `chat/router.py` | 125 | 125 | 0 | |
| `chat/web_search.py` | 56 | 56 | 0 | |
| **`classification.py`** | **85** | **111** | **+26** | **FLAG** |
| `extraction/__init__.py` | 2 | 2 | 0 | |
| **`extraction/entity_extractor.py`** | **522** | **459** | **-63** | **FLAG** |
| `extraction/llm_worker.py` | 127 | 127 | 0 | |
| `extraction/relation_builder.py` | 302 | 302 | 0 | |
| `mcp/__init__.py` | 10 | 10 | 0 | |
| `mcp/client.py` | 127 | 127 | 0 | |
| `mcp/local_cache.py` | 216 | 211 | -5 | |
| `mcp/manager.py` | 108 | 108 | 0 | |
| `mcp/relevance.py` | 114 | 114 | 0 | |
| `mcp/smithery.py` | 160 | 160 | 0 | |
| **`mcp/translate.py`** | **279** | **263** | **-16** | **FLAG** |
| `pipeline/__init__.py` | 3 | 3 | 0 | |
| `pipeline/chunker.py` | 116 | 116 | 0 | |
| `pipeline/embedder.py` | 245 | 245 | 0 | |
| `pipeline/engine.py` | 415 | 415 | 0 | |
| `pipeline/image_extractor.py` | 247 | 247 | 0 | |
| **`pipeline/ingest.py`** | **36** | **9** | **-27** | **FLAG** |
| **`pipeline/ingest_stages.py`** | **308** | **272** | **-36** | **FLAG** |
| `pipeline/language_filter.py` | 154 | 154 | 0 | |
| `pipeline/markdown_render.py` | 90 | 90 | 0 | |
| `pipeline/ocr_engine.py` | 74 | 74 | 0 | |
| `pipeline/parser.py` | 372 | 372 | 0 | |
| `plugins/__init__.py` | 111 | 111 | 0 | |
| `plugins/hooks.py` | 87 | 87 | 0 | |
| `plugins/host.py` | 176 | 176 | 0 | |
| `plugins/lifecycle.py` | 85 | 85 | 0 | |
| `plugins/registry.py` | 116 | 116 | 0 | |
| `retrieval/__init__.py` | 3 | 3 | 0 | |
| `retrieval/graph_recall.py` | 172 | 172 | 0 | |
| `retrieval/ranking.py` | 195 | 195 | 0 | |
| **`retrieval/rerank.py`** | **200** | **182** | **-18** | **FLAG** |
| `retrieval/search.py` | 194 | 194 | 0 | |
| `storage/__init__.py` | 6 | 6 | 0 | |
| `storage/chroma_store.py` | 166 | 166 | 0 | |
| **`storage/db.py`** | **1023** | **234** | **-789** | **FLAG** |
| `storage/tokenizer.py` | 92 | 92 | 0 | |

### Summary of flagged files (8 files with >10-line difference)

| File | Diff | Likely Explanation |
|------|-----:|---------------------|
| `storage/db.py` | -789 | Massive reduction — logic split into `chunks.py`, `conversations.py`, `entities.py`, `files.py`, `tasks.py` |
| `chat/engine.py` | -105 | Logic extracted into new `chat/orchestrator.py` (145 lines) |
| `extraction/entity_extractor.py` | -63 | Code reduced/consolidated |
| `pipeline/ingest_stages.py` | -36 | Simplified during refactor |
| `pipeline/ingest.py` | -27 | Grew much smaller (36 → 9 lines), likely restructured |
| `classification.py` | +26 | Expanded with new logic |
| `retrieval/rerank.py` | -18 | Slightly trimmed |
| `mcp/translate.py` | -16 | Minor reduction |

## 4. New `api/` Subdirectory (does NOT exist in original)

The original project has a single monolithic `api.py` (549 lines) at the src root. The refactored version **removes** `api.py` entirely and replaces it with a proper `api/` package containing 7 files (455 lines total):

| File | Lines | Purpose |
|------|------:|---------|
| `api/__init__.py` | 22 | Package init / router aggregation |
| `api/auth.py` | 45 | Authentication endpoints |
| `api/chat.py` | 66 | Chat endpoints |
| `api/conversations.py` | 44 | Conversation CRUD endpoints |
| `api/documents.py` | 145 | Document management endpoints |
| `api/graph.py` | 45 | Knowledge graph endpoints |
| `api/search.py` | 88 | Search endpoints |
| **Total** | **455** | |

**Note**: The refactored `api/` package (455 lines) is 94 lines shorter than the original monolithic `api.py` (549 lines), indicating some logic was moved to other modules (e.g., `chat/orchestrator.py`, `llm.py`) rather than all landing in the API layer.

---

## Summary Statistics

| Metric | Original | Refactored |
|--------|--------:|----------:|
| Total .py files | 51 | 64 |
| Shared files (both) | 50 | 50 |
| Files only in original | 1 | — |
| Files only in refactored | — | 14 |
| Files with >10-line diff | — | 8 |
| Files identical (0 diff) | — | 38 |
