import sys, asyncio
sys.path.insert(0, ".")
from src.retrieval.search import search

async def main():
    query = "连接器"
    print(f"=== Full search() for: {query!r} ===")
    results = await search(query, top_k=5)
    print(f"search() returned: {len(results)} results")
    for r in results[:3]:
        print(f"  id={r.get('id')} file={r.get('file_name','?')} score={r.get('score',0):.4f}")

    # Also test with collect_detail
    print(f"\n=== search(collect_detail=True) for: {query!r} ===")
    detail = await search(query, top_k=5, collect_detail=True)
    print(f"route: {detail.get('route')}")
    print(f"kind: {detail.get('kind')}")
    bm25_count = len(detail.get("recalls", {}).get("bm25", []))
    vec_count = len(detail.get("recalls", {}).get("vector", []))
    final_count = len(detail.get("final", []))
    print(f"BM25: {bm25_count}, Vector: {vec_count}, Final: {final_count}")

asyncio.run(main())
