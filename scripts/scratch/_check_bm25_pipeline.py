import sys, asyncio
sys.path.insert(0, ".")
from src.storage.db import fts_search
from src.retrieval.search import _dedup_by_file, _bm25_search

# Test BM25 search through the pipeline
query = "连接器"
print(f"=== Testing BM25 pipeline for: {query!r} ===")

# Step 1: Raw fts_search
raw = fts_search(query, limit=200)
print(f"fts_search raw: {len(raw)} results")
for r in raw[:3]:
    print(f"  id={r['id']} file={r.get('file_name','?')} score={r.get('score',0):.2f}")

# Step 2: _bm25_search wrapper
bm25 = _bm25_search(query, limit=200)
print(f"_bm25_search: {len(bm25)} results")

# Step 3: _dedup_by_file
deduped = _dedup_by_file(bm25, per_file=8)
print(f"_dedup_by_file: {len(deduped)} results")
for r in deduped[:3]:
    print(f"  id={r['id']} file={r.get('file_name','?')} score={r.get('score',0):.2f}")
