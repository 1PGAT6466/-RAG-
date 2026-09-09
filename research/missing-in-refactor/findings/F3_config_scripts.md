# F3: Configuration, Scripts, Docs, Tests, Plugins, Frontend Comparison

**Date**: 2026-08-21
**Original**: `E:\更新RAG框架`
**Refactored**: `E:\更新RAG框架\重构文件`

---

## 1. `.env` — DIFFERENT

The .env files are **NOT identical**. Both contain real API keys and secrets (not placeholders).

### Keys in BOTH (33 keys)
`BM25_WEIGHT`, `DATA_DIR`, `DB_PATH`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, `DEEPSEEK_TIMEOUT`, `EMBEDDING_DEVICE`, `EMBEDDING_MODEL`, `EMBED_REMOTE_THRESHOLD`, `HOST`, `JWT_SECRET`, `MIMO_API_KEY`, `MIMO_BASE_URL`, `MIMO_MODEL`, `PORT`, `RAG_AUTO_PREINDEX`, `RAG_AUTO_SUMMARY`, `RAG_AUTO_TAG`, `RAG_CHROMA`, `RAG_DYNAMIC_RANKING`, `RAG_ENTITY_EXTRACT`, `RAG_ENTITY_LLM`, `RAG_ENTITY_LLM_INTERVAL`, `RAG_ENTITY_LLM_MAX_CHUNKS`, `RAG_ENTITY_LLM_WORKERS`, `RAG_JIEBA`, `RAG_RERANK`, `SILICONFLOW_API_KEY`, `SILICONFLOW_BASE_URL`, `SMITHERY_API_KEY`, `UPLOAD_DIR`, `VECTOR_WEIGHT`

### Keys ONLY in refactored (2 extra)
- `SEARCH_TOP_K` — retrieval top-K parameter
- `TAVILY_API_KEY` — web search API key

### Keys ONLY in original
None.

### Structural differences
- Original has UTF-8 BOM (`EF BB BF`) + CRLF line endings
- Refactored has no BOM + LF line endings
- API key values, JWT secrets, and base URLs differ between the two files

---

## 2. `config.py` — IDENTICAL

Binary comparison: **no differences** (152 lines each).

---

## 3. `server.py` — IDENTICAL

Binary comparison: **no differences** (183 lines each).

---

## 4. `requirements.txt` — IDENTICAL

Binary comparison: **no differences** (43 lines each).

---

## 5. `scripts/` — IDENTICAL

Both have **15 files**. All 15 common files are binary-identical.

| File | Status |
|------|--------|
| `_watch_and_bench.py` | Same |
| `backfill_images.py` | Same |
| `cleanup_chroma_orphans.py` | Same |
| `count_lines.py` | Same |
| `e2e_verify.py` | Same |
| `migrate_classification.py` | Same |
| `migrate_stage1.py` | Same |
| `mini_mcp_server.py` | Same |
| `rebuild_pdf_ocr.py` | Same |
| `reclassify_manuals.py` | Same |
| `reset_data.py` | Same |
| `smoke_test.py` | Same |
| `start_service.cmd` | Same |
| `stop_service.cmd` | Same |
| `watch_folder.py` | Same |

**Files only in original**: none
**Files only in refactored**: none

---

## 6. `tests/` — IDENTICAL (source files)

Both have **4 test source files**. All 4 are binary-identical.

| File | Status |
|------|--------|
| `test_classification.py` | Same |
| `test_entity_extraction.py` | Same |
| `test_retrieval.py` | Same |
| `test_tokenizer.py` | Same |

**Files only in original**: `__pycache__/` directory with 4 `.pyc` files (compiled cache artifacts, not source)
**Files only in refactored**: none

---

## 7. `docs/` — IDENTICAL

Both have **7 files**. All 7 are binary-identical.

| File | Status |
|------|--------|
| `插件系统设计文档.md` | Same |
| `工业知识库三层架构设计文档.md` | Same |
| `阶段收获蒸馏.md` | Same |
| `五项改进整体方案.md` | Same |
| `新旧框架对照表.xlsx` | Same |
| `组件移植清单.md` | Same |
| `Obsidian图谱与文件管理调研报告.md` | Same |

---

## 8. `plugins/` — IDENTICAL (source files)

Both have 2 plugin directories (`example-plugin/`, `hook-demo/`). All 5 source files are binary-identical.

**Files only in original**: `__pycache__/main.cpython-311.pyc` in both plugin dirs (2 files, compiled cache)
**Files only in refactored**: none

---

## 9. `frontend/` — IDENTICAL

### Root files (4 each, all identical)
| File | Status |
|------|--------|
| `index.html` | Same |
| `package.json` | Same |
| `package-lock.json` | Same |
| `vite.config.js` | Same |

### `frontend/src/` (19 files each, all identical)
All subdirectories match: `api/`, `App.vue`, `assets/`, `components/`, `main.js`, `router.js`, `stores/`, `styles/`, `views/`

### Only in original (root level)
- `node_modules/` — installed dependencies (runtime artifact)
- `dist/` — build output (runtime artifact)

The refactored version also has `dist/` but lacks `node_modules/`. Neither is source — both are generated artifacts.

---

## 10. `AGENTS.md`, `MEMORY.md`, `.gitignore` — ALL IDENTICAL

| File | Lines | Bytes | Binary Match |
|------|-------|-------|-------------|
| `AGENTS.md` | 180 | — | Identical |
| `MEMORY.md` | 868 | 50,023 | Identical |
| `.gitignore` | 34 | — | Identical |

---

## 11. `skills/`, `research/`, `watch_inbox/`

### `skills/` — IDENTICAL (both empty)

### `watch_inbox/` — IDENTICAL
Both contain only `.watched.json`.

### `research/` — DIFFERENT

Both share these common files (all binary-identical):
- `rag-performance-optimization/REPORT.md`
- `rag-performance-optimization/brief.md`
- `rag-performance-optimization/findings/F1.md` through `F5.md`
- `refactor-comparison/REPORT.md`

| Delta | Files |
|-------|-------|
| **Only in original** | `missing-in-refactor/brief.md`, `missing-in-refactor/findings/F1_toplevel.md`, `missing-in-refactor/findings/F2_src_code.md` |
| **Only in refactored** | `refactor-verification/contract.md`, `refactor-verification/F1_import_chains.md`, `refactor-verification/F2_signatures.md`, `refactor-verification/F3_broken_refs.md`, `refactor-verification/F4_routes.md`, `refactor-verification/REPORT.md` |

The original has research about what's missing in the refactor; the refactored has a `refactor-verification/` directory with 6 new files covering import chains, signatures, broken refs, routes, and a verification report.

---

## Summary Table

| Item | Status | Notes |
|------|--------|-------|
| `.env` | **DIFFERENT** | 2 extra keys in refactored (SEARCH_TOP_K, TAVILY_API_KEY); different encoding (BOM vs no-BOM); different key values |
| `config.py` | Identical | 152 lines |
| `server.py` | Identical | 183 lines |
| `requirements.txt` | Identical | 43 lines |
| `scripts/` | Identical | 15/15 files match |
| `tests/` | Identical (source) | 4/4 test files match; original has extra `__pycache__/` |
| `docs/` | Identical | 7/7 files match |
| `plugins/` | Identical (source) | 5/5 source files match; original has extra `__pycache__/` |
| `frontend/` | Identical (source) | 23/23 source files match; `node_modules/` absent from refactored |
| `AGENTS.md` | Identical | |
| `MEMORY.md` | Identical | 868 lines, 50,023 bytes |
| `.gitignore` | Identical | |
| `skills/` | Identical | Both empty |
| `research/` | **DIFFERENT** | Original has `missing-in-refactor/`; Refactored has `refactor-verification/` (6 new files) |
| `watch_inbox/` | Identical | |

**Bottom line**: The refactored copy is a faithful replica of the original's source code and configuration, with only two intentional differences:
1. The `.env` file contains slightly different keys (added `SEARCH_TOP_K`, `TAVILY_API_KEY`) and is reformatted (no BOM, LF line endings).
2. The `research/` directory has different project-management artifacts (original tracks what's missing; refactored has a verification report).
3. Runtime artifacts (`__pycache__/`, `node_modules/`) were not copied to the refactored version.
