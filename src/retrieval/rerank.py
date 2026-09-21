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
from datetime import datetime

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
        from src.llm_client import call_llm, extract_json
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
        raw = scores[i]
        # #11（2026-09-21）：统一打分刻度。DeepSeek 返回 0-10，归一化到 0-1，
        # 与 SiliconFlow（BGE-Reranker relevance_score 本就 0-1）对齐，
        # 避免不同降级路径下 score 量纲不一致导致排序跳变。
        norm = max(0.0, min(1.0, raw / 10.0))
        rr["_rerank_score"] = round(norm, 4)
        rr["_rerank_source"] = "deepseek"
        rr["_pre_rerank_score"] = rr.get("score", 0)
        rr["score"] = round(norm, 4)
        scored.append((norm, rr))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def rerank_local(query, candidates, top_k=30):
    """L3: 本地 TF-IDF + jieba 精排（零依赖兜底）

    算法：
    1. jieba 分词 query → 计算每个 token 在文档中的 TF-IDF 分数
    2. 对 TF-IDF 分数和原始 RRF 分数分别做 min-max 归一化
    3. 融合公式：score = original * 0.6 + tfidf * 0.4
    """
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

    # #11（2026-09-21）：改为词边界匹配（jieba 分词后按 token 集合匹配），
    # 避免子串误命中（如查询 token「镀金」命中文本里的「镀金层」）。
    def _tokenize_doc(doc_text: str) -> list:
        try:
            import jieba
            return [t.lower() for t in jieba.cut_for_search(doc_text) if t.strip()]
        except ImportError:
            return doc_text.split()

    from collections import Counter
    scored = []
    raw_scores = []
    for r in candidates:
        text = _text_of(r).lower()
        if not text:
            scored.append((r.get("score", 0), r, 0.0))
            raw_scores.append(0.0)
            continue
        doc_tokens = _tokenize_doc(text)
        if not doc_tokens:
            scored.append((r.get("score", 0), r, 0.0))
            raw_scores.append(0.0)
            continue
        doc_counter = Counter(doc_tokens)
        doc_token_set = set(doc_tokens)
        doc_len = max(len(doc_tokens), 1)
        score = 0.0
        for t in tokens:
            if t in doc_token_set:
                # 词边界命中：t 作为完整 token 出现
                score += doc_counter[t] / doc_len * 1000
                score += 5
            elif len(t) >= 2:
                # 降级：t 是某个文档 token 的真子串时给半权重（保留 CJK 未登录词召回）
                for dt in doc_token_set:
                    if t != dt and t in dt:
                        score += 2.5
                        break
        raw_scores.append(score)
        scored.append((0, r, score))

    # 归一化 TF-IDF 分数到 [0, 1]，消除量纲差异后再融合
    max_raw = max(raw_scores) if raw_scores else 1.0
    min_raw = min(raw_scores) if raw_scores else 0.0
    span = max_raw - min_raw if max_raw > min_raw else 1.0

    # S2: 对 original (RRF 分) 也做 min-max 归一化，使两个分数量纲统一到 [0,1]
    originals = [float(r.get("score", 0)) for _, r, _ in scored]
    max_orig = max(originals) if originals else 0.0

    result = []
    for idx, (_, r, raw) in enumerate(scored):
        original = originals[idx]
        norm_score = (raw - min_raw) / span  # 归一化到 [0,1]
        norm_original = original / max_orig if max_orig > 0 else original
        rr = dict(r)
        rr["_rerank_score"] = round(raw, 4)
        rr["_rerank_source"] = "local-tfidf"
        rr["score"] = round(norm_original * 0.6 + norm_score * 0.4, 4)
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
    # #14（2026-09-21）：query 归一化（strip/lower/全半角/空白折叠），避免大小写标点差异 miss
    cache_key = (_normalize_query(query), top_k, fp)
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
_RERANK_CACHE_TTL = 300  # 秒（内存层）
_RERANK_DB_CACHE_TTL = 24 * 3600  # 秒（SQLite 持久层，#8：24h 过期）


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


def _normalize_query(query: str) -> str:
    """#14（2026-09-21）：rerank 缓存 key 的 query 归一化。

    统一 strip → 全角转半角 → 小写 → 空白折叠，使「镀金  层」、「镀金层」、
    「ＡＢＣ」/「abc」等语义相同的查询共享缓存条目，降低 miss 率。
    """
    if not query:
        return ""
    q = str(query).strip()
    # 全角字符（U+FF01–U+FF5E）转半角；全角空格 U+3000 转普通空格
    out = []
    for ch in q:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    q = "".join(out).lower()
    return re.sub(r"\s+", " ", q).strip()


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
    """确保表存在。

    缺陷A（2026-09-21）：此处曾有与 connection.py SCHEMA 冲突的建表定义
    （q/top_k/fp/results_json），因表已存在而静默 no-op。现统一以
    `src.storage.connection.SCHEMA` 为唯一权威定义，本函数仅保留作为兜底。
    """
    from src.storage.connection import SCHEMA
    from src.storage.db import _get_conn
    conn = _get_conn()
    conn.executescript(SCHEMA)
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
    返回的是「排序元数据」，调用方据此重排当次的 candidates（保证内容实时）。

    #8（2026-09-21）：增加 TTL 校验。旧实现无过期，候选集不变时排序永久冻结
    （新反馈惩罚 / 权限变化不会反映）。超 TTL 视为 miss 并顺手删除该行。
    """
    import json as _json
    q, top_k, fp = key
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT results_json, created_at FROM rerank_cache WHERE q=? AND top_k=? AND fp=?",
            (q, top_k, fp)
        ).fetchone()
        if not row:
            return None
        if _rerank_db_row_expired(row["created_at"]):
            conn.execute(
                "DELETE FROM rerank_cache WHERE q=? AND top_k=? AND fp=?", (q, top_k, fp)
            )
            conn.commit()
            return None
        return _json.loads(row["results_json"])
    except Exception as e:
        logger.debug(f"rerank 持久化缓存读取失败: {e}")
        return None


def _rerank_db_row_expired(created_at: str | None) -> bool:
    """判断持久缓存行是否超过 TTL。

    #2（2026-09-21）：created_at 已统一为 UTC（SQLite `datetime('now')`），
    故按 UTC 解释后与当前 epoch 对比。
    """
    if not created_at:
        return False
    try:
        from datetime import timezone
        ts = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except (ValueError, TypeError):
        return False
    return (time.time() - ts) > _RERANK_DB_CACHE_TTL


def purge_expired_rerank_cache() -> int:
    """清理持久缓存中过期的行（供启动 / 每日任务调用）。返回删除行数。"""
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        cutoff = datetime.utcfromtimestamp(time.time() - _RERANK_DB_CACHE_TTL).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cur = conn.execute("DELETE FROM rerank_cache WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0
    except Exception as e:
        logger.debug(f"rerank 持久缓存清理失败: {e}")
        return 0
