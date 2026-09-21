import sys
sys.path.insert(0, ".")
from src.storage.db import fts_search

queries = ["连接器", "供应商", "Activar", "镀金层厚度要求"]
for q in queries:
    results = fts_search(q, limit=5)
    print(f"fts_search({q!r}): {len(results)} results")
    for r in results[:2]:
        print(f"  id={r['id']} file={r.get('file_name','?')} score={r.get('score',0):.2f}")
