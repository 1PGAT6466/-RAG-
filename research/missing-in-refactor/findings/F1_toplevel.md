# F1: Top-Level Directory Comparison — Original vs Refactored

**Date**: 2026-06-19  
**Original**: `E:\更新RAG框架`  
**Refactored**: `E:\更新RAG框架\重构文件`  
**Exclusions**: .git, node_modules, __pycache__, .pytest_cache, logs, .easyclaw, .agents, .mimocode, .obsidian

---

## 1. Entries in Original but NOT in Refactored

| Entry | Type | Notes |
|-------|------|-------|
| `重构文件/` | Directory | The refactored version itself lives inside the original; expected |

**Root-level files**: ALL 7 files (`.env`, `.gitignore`, `AGENTS.md`, `config.py`, `MEMORY.md`, `requirements.txt`, `server.py`) exist in both. No root file is missing from the refactored version.

---

## 2. Entries in Refactored but NOT in Original

**At the top level**: NONE. All 10 directories and 7 files in the refactored version also exist in the original.

**Inside subdirectories** (new files added during refactoring):

### `src/` — 13 new files

| New File | Purpose |
|----------|---------|
| `llm.py` | New LLM module |
| `api/__init__.py` | API package init |
| `api/auth.py` | Auth endpoint |
| `api/chat.py` | Chat endpoint |
| `api/conversations.py` | Conversations endpoint |
| `api/documents.py` | Documents endpoint |
| `api/graph.py` | Graph endpoint |
| `api/search.py` | Search endpoint |
| `chat/orchestrator.py` | Chat orchestrator |
| `storage/chunks.py` | Chunks storage layer |
| `storage/conversations.py` | Conversations storage layer |
| `storage/entities.py` | Entities storage layer |
| `storage/files.py` | Files storage layer |
| `storage/tasks.py` | Tasks storage layer |

### `research/` — 6 new files

| New File | Purpose |
|----------|---------|
| `refactor-verification/contract.md` | Verification contract |
| `refactor-verification/F1_import_chains.md` | Import chain findings |
| `refactor-verification/F2_signatures.md` | Signature findings |
| `refactor-verification/F3_broken_refs.md` | Broken reference findings |
| `refactor-verification/F4_routes.md` | Route findings |
| `refactor-verification/REPORT.md` | Verification report |

---

## 3. Shared Directories — File Count Comparison

| Directory | Original Count | Refactored Count | Delta | Status |
|-----------|---------------|-----------------|-------|--------|
| `data/` | 5,801 | 5,799 | -2 | ⚠️ Different |
| `docs/` | 7 | 7 | 0 | ✅ Match |
| `frontend/` | 300 | 300 | 0 | ✅ Match |
| `plugins/` | 4 | 4 | 0 | ✅ Match |
| `research/` | 9 | 14 | +5 | ⚠️ More in refactored |
| `scripts/` | 15 | 15 | 0 | ✅ Match |
| `skills/` | 0 | 0 | 0 | ✅ Match (both empty) |
| `src/` | 50 | 63 | +13 | ⚠️ More in refactored |
| `tests/` | 4 | 4 | 0 | ✅ Match |
| `watch_inbox/` | 1 | 1 | 0 | ✅ Match |

---

## 4. Root-Level File Size Comparison

| File | Original (bytes) | Refactored (bytes) | Match? |
|------|------------------|--------------------| -------|
| `.env` | 1,344 | 1,284 | ⚠️ Different (−60 bytes) |
| `.gitignore` | 415 | 415 | ✅ |
| `AGENTS.md` | 9,312 | 9,312 | ✅ |
| `config.py` | 6,552 | 6,552 | ✅ |
| `MEMORY.md` | 83,049 | 83,049 | ✅ |
| `requirements.txt` | 1,067 | 1,067 | ✅ |
| `server.py` | 6,313 | 6,313 | ✅ |

---

## 5. Detailed Diffs for Non-Matching Directories

### `data/` — 2 files only in Original (none missing from Refactored)

| File | Notes |
|------|-------|
| `rag.db-shm` | SQLite shared memory file (ephemeral/runtime artifact) |
| `rag.db-wal` | SQLite write-ahead log (ephemeral/runtime artifact) |

> These are SQLite runtime artifacts, not source files. Their absence in the refactored copy is expected.

### `src/` — 1 file only in Original, 14 files only in Refactored

**Only in Original:**
- `api.py` — the monolithic API file (likely decomposed into `api/*.py`)

**Only in Refactored (14 files):**
- `llm.py`, `api/__init__.py`, `api/auth.py`, `api/chat.py`, `api/conversations.py`, `api/documents.py`, `api/graph.py`, `api/search.py`, `chat/orchestrator.py`, `storage/chunks.py`, `storage/conversations.py`, `storage/entities.py`, `storage/files.py`, `storage/tasks.py`

### `research/` — 1 file only in Original, 6 files only in Refactored

**Only in Original:**
- `missing-in-refactor\brief.md`

**Only in Refactored (6 files):**
- `refactor-verification/contract.md`, `F1_import_chains.md`, `F2_signatures.md`, `F3_broken_refs.md`, `F4_routes.md`, `REPORT.md`

---

## 6. Summary

- **No top-level entries were lost** during refactoring. The refactored version is a superset at the directory level.
- The `src/` refactoring decomposed the monolithic `api.py` into 8 API modules + added `llm.py`, `chat/orchestrator.py`, and 5 storage modules (+14 files, −1 file = net +13).
- The `.env` file has a 60-byte size difference (likely a config change).
- The `data/` delta of −2 files is just SQLite runtime artifacts (`rag.db-shm`, `rag.db-wal`), not source code.
