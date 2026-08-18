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

import httpx

logger = logging.getLogger("rag.rerank")

# 统一从 config 读取（config 负责 load_dotenv），避免 os.getenv 在独立进程/未加载 .env 时读不到密钥
from config import (
    SILICONFLOW_API_KEY, SILICONFLOW_BASE_URL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_FLASH_MODEL,
)


def _text_of(item: dict) -> str:
    """统一取 chunk 文本（兼容 text / content 字段）"""
    return (item.get("content") or item.get("text") or "").strip()


async def rerank_with_siliconflow(query, candidates, top_k=30):
    """L1: SiliconFlow BGE-Reranker（专用重排模型）"""
    if not SILICONFLOW_API_KEY or not candidates:
        return []
    documents = [_text_of(r)[:1024] for r in candidates]
    if not any(d for d in documents):
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
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
    """L2: DeepSeek Chat 相关性打分（批量，一次请求）"""
    if not candidates or not DEEPSEEK_API_KEY:
        return []
    documents = [_text_of(r)[:800] for r in candidates]
    if not any(d for d in documents):
        return []

    doc_list = "\n".join([f"[{i}] {d[:400]}" for i, d in enumerate(documents[:30])])
    prompt = (
        "对以下文档片段与查询的相关性打分（0-10分，10=完全相关）：\n"
        f"查询：{query[:200]}\n\n"
        f"文档：\n{doc_list}\n\n"
        '返回 JSON 数组：{"scores": [分数1, 分数2, ...]}，只输出 JSON。'
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_FLASH_MODEL,  # 轻量非推理模型，适合结构化打分（避免 reasoning 抢占 max_tokens）
                    "messages": [
                        {"role": "system", "content": "只输出纯 JSON。你是相关性打分引擎。"},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
                raw = re.sub(r"```(?:json)?\s*|```", "", raw).strip()
                scores_data = json.loads(raw)
                scores = scores_data.get("scores", [])
                scored = []
                for i, r in enumerate(candidates[:len(scores)]):
                    s = float(scores[i]) if i < len(scores) else 0.0
                    rr = dict(r)
                    rr["_rerank_score"] = round(s, 4)
                    rr["_rerank_source"] = "deepseek"
                    scored.append((s, rr))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [r for _, r in scored[:top_k]]
    except Exception as e:
        logger.debug(f"[Rerank DeepSeek] failed: {e}")
    return []


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
    for r in candidates:
        text = _text_of(r).lower()
        if not text:
            scored.append((r.get("score", 0), r))
            continue
        score = 0.0
        text_len = max(len(text), 1)
        for t in tokens:
            count = text.count(t)  # 一次 count 既得词频又可判断存在（省掉重复的 in 判断）
            score += count / text_len * 1000
            if count:
                score += 5
        original = float(r.get("score", 0))
        rr = dict(r)
        rr["_rerank_score"] = round(score, 4)
        rr["_rerank_source"] = "local-tfidf"
        rr["score"] = round(original * 0.5 + score * 0.5, 2)
        scored.append((rr["score"], rr))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


async def rerank(query, candidates, top_k=30):
    """统一 Rerank 入口：SiliconFlow → DeepSeek → 本地 TF-IDF"""
    if not candidates:
        return candidates

    results = await rerank_with_siliconflow(query, candidates, top_k)
    if results:
        return results

    results = await rerank_with_deepseek(query, candidates, top_k)
    if results:
        return results

    return rerank_local(query, candidates, top_k)
