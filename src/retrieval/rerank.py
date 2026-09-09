"""
rerank.py — Rerank 精排降级链（移植自 RAG伏羲 src/taiyang/rerank.py）

优先级（三级降级）：
  1. SiliconFlow BGE-Reranker（专用重排模型，快、准、便宜）
  2. DeepSeek LLM 相关性打分（云端兜底）
  3. 本地 TF-IDF + jieba（零依赖，纯 CPU 终极兜底）

说明：原框架第四级 embedder_server Bi-Encoder 依赖独立服务，新框架无该服务，
     故降级链裁剪为三级。

字段适配：原框架 chunk 文本字段为 "text"，新框架为 "content"。
"""
import re
import json
import logging
import time
import threading
from collections import OrderedDict

import httpx

logger = logging.getLogger("rag.rerank")

# 统一从 config 读取（config 负责 load_dotenv），避免 os.getenv 在独立进程/未加载 .env 时读不到密钥
from config import (
    SILICONFLOW_API_KEY, SILICONFLOW_BASE_URL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_FLASH_MODEL,
)

# DeepSeek 相关性打分单批候选数上限（控制单次 LLM prompt 大小，避免超长上下文/打分数丢失）
_DEEPSEEK_BATCH = 30


def _text_of(item: dict) -> str:
    """统一取 chunk 文本（兼容 text / content 字段）"""
    return (item.get("content") or item.get("text") or "").strip()


async def rerank_with_siliconflow(query, candidates, top_k=30):
    """L1: SiliconFlow BGE-Reranker（专用重排模型）"""
    if not SILICONFLOW_API_KEY or not candidates:
        return []
    documents = [_text_of(r)[:512] for r in candidates]
    if not any(d for d in documents):
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{SILICONFLOW_BASE_URL}/rerank",
                headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "BAAI/bge-reranker-v2-m3",
                    "query": query[:512],
                    "documents": documents,
                    "top_n": min(top_k, len(documents)),
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", []):
                    idx = item["index"]
                    score = item["relevance_score"]
                    if idx < len(candidates):
                        r = dict(candidates[idx])
                        r["_rerank_score"] = round(float(score), 4)
                        r["_rerank_source"] = "siliconflow"
                        # 关键：把 rerank 相关性分写入 score 主字段，作为下游统一排序依据，
                        # 避免「返回顺序已按 rerank 排，但 score 仍残留 post_rank 的 boost 大数」
                        # 导致排序与分数脱节。原 post_rank 分数保留在 _pre_rerank_score 供追溯。
                        r["_pre_rerank_score"] = r.get("score", 0)
                        r["score"] = round(float(score), 4)
                        results.append(r)
                if results:
                    logger.info(f"[Rerank SiliconFlow] {len(results)} results, top={results[0]['_rerank_score']}")
                return results[:top_k]
            else:
                logger.warning(f"[Rerank SiliconFlow] HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[Rerank SiliconFlow] failed: {e}")
    return []


async def rerank_with_deepseek(query, candidates, top_k=30):
    """L2: DeepSeek Chat 相关性打分（分批，每批 ≤ _DEEPSEEK_BATCH，合并排序）。

    历史 bug（缺陷15）：原实现 `documents[:30]` 只把前 30 候选发给 LLM 打分，
    但结果循环遍历全部 candidates，导致第 31+ 候选 scores 越界拿到 0 分——
    与 search.py 的 `RERANK_TOP_K_MULTIPLIER`（候选池 = top_k×4）设计意图矛盾。
    此处改为分批打分：所有候选都被打分，scores 与 candidates 严格一一对齐。
    """
    if not candidates or not DEEPSEEK_API_KEY:
        return []
    documents = [_text_of(r)[:800] for r in candidates]
    if not any(d for d in documents):
        return []

    scores = [0.0] * len(candidates)  # 预填，保证与 candidates 严格对齐
    try:
        from src.llm import call_llm, extract_json
        for start in range(0, len(candidates), _DEEPSEEK_BATCH):
            batch = documents[start:start + _DEEPSEEK_BATCH]
            doc_list = "\n".join(f"[{i}] {d[:400]}" for i, d in enumerate(batch))
            prompt = (
                "对以下文档片段与查询的相关性打分（0-10分，10=完全相关）：\n"
                f"查询：{query[:200]}\n\n"
                f"文档：\n{doc_list}\n\n"
                '返回 JSON 数组：{"scores": [分数1, 分数2, ...]}，只输出 JSON。'
            )
            raw = await call_llm([
                {"role": "system", "content": "只输出纯 JSON。你是相关性打分引擎。"},
                {"role": "user", "content": prompt},
            ], max_tokens=500, temperature=0)
            scores_data = extract_json(raw, expect="object")
            if not scores_data or "scores" not in scores_data:
                logger.warning(f"[Rerank DeepSeek] 批次 {start} 输出无合法 scores JSON: {raw[:80]!r}")
                continue
            batch_scores = scores_data.get("scores", [])
            for j in range(len(batch)):
                try:
                    scores[start + j] = float(batch_scores[j]) if j < len(batch_scores) else 0.0
                except (TypeError, ValueError):
                    scores[start + j] = 0.0
    except Exception as e:
        logger.debug(f"[Rerank DeepSeek] failed: {e}")
        return []

    scored = []
    for i, r in enumerate(candidates):
        rr = dict(r)
        rr["_rerank_score"] = round(scores[i], 4)
        rr["_rerank_source"] = "deepseek"
        rr["_pre_rerank_score"] = rr.get("score", 0)
        rr["score"] = round(scores[i], 4)  # 与 SiliconFlow 路径一致：rerank 分写入主字段
        scored.append((scores[i], rr))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def rerank_local(query, candidates, top_k=30):
    """L3: 本地 TF-IDF + jieba 精排（零依赖兜底）"""
    if not candidates:
        return candidates

    tokens = []
    try:
        import jieba
        jieba.setLogLevel(20)
        tokens = [t.strip() for t in jieba.cut_for_search(query.strip()) if len(t.strip()) >= 1]
    except ImportError:
        tokens = query.lower().split()

    if not tokens:
        return candidates[:top_k]

    # 提前 lower 一次，避免循环内对每个 token 重复 .lower()
    tokens = [t.lower() for t in tokens]

    scored = []
    raw_scores = []
    for r in candidates:
        text = _text_of(r).lower()
        if not text:
            scored.append((r.get("score", 0), r, 0.0))
            raw_scores.append(0.0)
            continue
        score = 0.0
        text_len = max(len(text), 1)
        for t in tokens:
            count = text.count(t)
            score += count / text_len * 1000
            if count:
                score += 5
        raw_scores.append(score)
        scored.append((0, r, score))

    # 归一化 TF-IDF 分数到 [0, 1]，消除量纲差异后再融合
    max_raw = max(raw_scores) if raw_scores else 1.0
    min_raw = min(raw_scores) if raw_scores else 0.0
    span = max_raw - min_raw if max_raw > min_raw else 1.0

    result = []
    for _, r, raw in scored:
        original = float(r.get("score", 0))
        norm_score = (raw - min_raw) / span  # 归一化到 [0,1]
        rr = dict(r)
        rr["_rerank_score"] = round(raw, 4)
        rr["_rerank_source"] = "local-tfidf"
        rr["score"] = round(original * 0.6 + norm_score * 0.4, 4)
        result.append(rr)

    result.sort(key=lambda x: x["score"], reverse=True)
    return result[:top_k]


async def rerank(query, candidates, top_k=30):
    """统一 Rerank 入口：SiliconFlow → DeepSeek → 本地 TF-IDF

    结果级缓存：两级缓存（内存 LRU + SQLite 持久化），key = (query, top_k, 候选指纹)。
    - 内存 LRU：同进程内命中，毫秒级返回（原有）。
    - SQLite 持久化：跨进程重启复用，同 query 永不重复调远程 rerank。
    - 候选指纹：对候选 chunk id 集合做稳定 hash，候选集合变化时缓存自动失效
      （避免入库新文档后命中旧排序结果）。
    """
    if not candidates:
        return candidates

    # 候选指纹：candidates 的 chunk id 有序序列的 hash（保证候选集合变化即失效）
    fp = _candidate_fingerprint(candidates)

    # 结果级缓存（两级：内存 LRU + SQLite 持久化）
    cache_key = (query, top_k, fp)
    cached = _rerank_cache_get(cache_key)
    if cached is not None:
        logger.info(f"[Rerank] 缓存命中(内存): query='{query[:30]}'")
        return cached
    # SQLite 持久化缓存：只存排序元数据，命中时用当次 candidates 重排重建
    db_seq = _rerank_cache_db_get(cache_key)
    if db_seq is not None:
        reordered = _reorder_by_seq(candidates, db_seq, top_k)
        if reordered:
            logger.info(f"[Rerank] 缓存命中(SQLite): query='{query[:30]}'")
            _rerank_cache_set(cache_key, reordered)  # 回填内存，下次毫秒级
            return reordered

    results = await rerank_with_siliconflow(query, candidates, top_k)
    if results:
        _rerank_cache_set(cache_key, results)
        return results

    results = await rerank_with_deepseek(query, candidates, top_k)
    if results:
        _rerank_cache_set(cache_key, results)
        return results

    results = rerank_local(query, candidates, top_k)
    # 本地 TF-IDF 结果确定性高、代价低，也缓存以复用
    _rerank_cache_set(cache_key, results)
    return results


# === rerank 结果级 LRU 缓存 ===
_rerank_cache: OrderedDict = OrderedDict()  # key -> (ts, results)
_rerank_cache_lock = threading.Lock()
_RERANK_CACHE_MAX = 512
_RERANK_CACHE_TTL = 300  # 秒


def _rerank_cache_get(key):
    with _rerank_cache_lock:
        item = _rerank_cache.get(key)
        if item is None:
            return None
        ts, results = item
        if time.time() - ts > _RERANK_CACHE_TTL:
            _rerank_cache.pop(key, None)
            return None
        # LRU：移到末尾
        _rerank_cache.pop(key, None)
        _rerank_cache[key] = item
        return results


def _rerank_cache_set(key, results):
    with _rerank_cache_lock:
        _rerank_cache.pop(key, None)
        _rerank_cache[key] = (time.time(), results)
        while len(_rerank_cache) > _RERANK_CACHE_MAX:
            _rerank_cache.popitem(last=False)
    # 持久化到 SQLite（跨重启复用，失败静默降级到内存缓存）
    try:
        _rerank_cache_db_set(key, results)
    except Exception as e:
        logger.debug(f"rerank 缓存持久化失败（仅内存缓存）: {e}")


def _candidate_fingerprint(candidates: list[dict]) -> str:
    """候选集合指纹：chunk id 有序序列的稳定 hash。

    目的：候选集合（检索结果）变化时，缓存自动失效，避免命中过期排序。
    用 id 而非文本 hash——id 稳定且能反映「新增/删除 chunk」的变化。
    """
    import hashlib
    ids = [str(c.get("id")) for c in candidates if c.get("id") is not None]
    joined = ",".join(ids)
    return hashlib.md5(joined.encode()).hexdigest()[:12]


def _reorder_by_seq(candidates: list[dict], seq: list[dict], top_k: int) -> list[dict]:
    """用持久化缓存的排序序列表（id+score）重排当次 candidates。

    持久化缓存只存「排序元数据」，不存候选 content（内容当次检索实时获取，
    避免缓存大文本）。命中时按 seq 里的 id 顺序取出对应候选，并把缓存的
    rerank 分写回 score；未在 seq 里的候选（理论上不存在，因指纹已保证一致）
    追加到末尾。保证返回的 dict 含完整 content/file_name 等字段。
    """
    if not candidates or not seq:
        return []
    by_id = {c.get("id"): c for c in candidates if c.get("id") is not None}
    ordered = []
    seen = set()
    for s in seq:
        cid = s.get("id")
        if cid in by_id and cid not in seen:
            r = dict(by_id[cid])
            r["score"] = round(float(s.get("score", r.get("score", 0))), 4)
            r["_rerank_source"] = "cache"
            r["_rerank_score"] = round(float(s.get("score", 0)), 4)
            ordered.append(r)
            seen.add(cid)
    # 万一有候选不在 seq（指纹碰撞等极端情况），按原序追加兜底
    for c in candidates:
        if c.get("id") not in seen:
            ordered.append(dict(c))
    return ordered[:top_k] if ordered else []


def _rerank_cache_db_ensure_table():
    from src.storage.db import _get_conn
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rerank_cache (
            q TEXT NOT NULL,
            top_k INTEGER NOT NULL,
            fp TEXT NOT NULL,
            results_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (q, top_k, fp)
        )
    """)
    conn.commit()


def _rerank_cache_db_set(key, results):
    """写入持久化缓存。只存可序列化的核心字段（id + score + 顺号），
    回读时按顺号重建排序后的 chunk 引用，避免序列化整个候选 dict。"""
    import json as _json
    q, top_k, fp = key
    # 存 (id, score) 序列表；回读时以此为排序依据，候选内容由当次检索实时填充
    seq = [{"id": r.get("id"), "score": r.get("score")} for r in results]
    _rerank_cache_db_ensure_table()
    from src.storage.db import _get_conn
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO rerank_cache (q, top_k, fp, results_json) VALUES (?,?,?,?)",
        (q, top_k, fp, _json.dumps(seq, ensure_ascii=False))
    )
    conn.commit()


def _rerank_cache_db_get(key) -> list | None:
    """读取持久化缓存，返回排序序列表（id+score）。
    返回的是「排序元数据」，调用方据此重排当次的 candidates（保证内容实时）。"""
    import json as _json
    q, top_k, fp = key
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT results_json FROM rerank_cache WHERE q=? AND top_k=? AND fp=?",
            (q, top_k, fp)
        ).fetchone()
        if not row:
            return None
        return _json.loads(row["results_json"])
    except Exception as e:
        logger.debug(f"rerank 持久化缓存读取失败: {e}")
        return None
