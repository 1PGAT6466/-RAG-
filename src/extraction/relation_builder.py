"""
relation_builder.py — 关系构建（阶段 2）

职责：
  1. 把抽取出的实体入库（entities + entity_chunks + entity_files）
  2. 同一 chunk 内共现的实体之间建 cooccur 边（entity_relations）
  3. 文档相似度（可选，用向量余弦）建文档引用边
  4. 语义关系边（material→compatible_process、standard→category）

设计原则（对齐设计文档 §8）：
  - 关系判定 = Workflow（规则共现）+ Agent（语义关系，可选）
  - 失败可重跑、可降级（LLM 抽取失败 → 规则抽取）
"""
import logging
import asyncio
import re

from src.storage import db
from src.extraction import entity_extractor

logger = logging.getLogger("rag.extraction")


async def extract_and_store(chunk_id: int, chunk_content: str, file_id: int,
                            use_llm: bool = True) -> list[dict]:
    """抽取单个 chunk 的实体并入库，返回该 chunk 抽取出的实体列表"""
    entities = []
    if use_llm:
        try:
            entities = await entity_extractor.extract_llm(chunk_content)
        except Exception as e:
            logger.warning(f"LLM 实体抽取失败，降级规则抽取: {e}")
            entities = []
    if not entities:
        entities = entity_extractor.extract_rule(chunk_content)

    stored = _store_entities_and_edges(entities, chunk_id, file_id)
    return stored


def _store_entities_and_edges(entities: list[dict], chunk_id: int, file_id: int) -> list[dict]:
    """把抽取出的实体入库 + 建 cooccur 边（同步，可复用）

    返回带 id 的实体列表（stored）
    """
    entity_ids = []
    stored = []
    for ent in entities:
        eid = db.upsert_entity(
            name=ent["name"],
            etype=ent["type"],
            aliases=ent.get("aliases", []),
            description=ent.get("description", ""),
            attributes=ent.get("attributes", {}),
        )
        db.add_entity_chunk(eid, chunk_id, mention_count=1)
        db.add_entity_file(eid, file_id, mention_count=1)
        entity_ids.append(eid)
        stored.append({**ent, "id": eid})

    # 同一 chunk 内实体两两共现 → cooccur 边
    _build_cooccur_edges(entity_ids)
    return stored


def _build_cooccur_edges(entity_ids: list[int]) -> None:
    """共现建边：同一 chunk 内的实体两两建立 cooccur 关系"""
    n = len(entity_ids)
    for i in range(n):
        for j in range(i + 1, n):
            db.add_entity_relation(entity_ids[i], entity_ids[j], rel_type="cooccur", weight=1.0)


async def process_file(file_id: int, use_llm: bool = True) -> dict:
    """处理一个文件的全部 chunk：抽取 + 入库 + 建关系

    返回 {chunks_processed, entities_found}
    """
    chunks = db.get_chunks_by_file(file_id)
    total_entities = 0
    for c in chunks:
        ents = await extract_and_store(c["id"], c["content"], file_id, use_llm=use_llm)
        total_entities += len(ents)
        if use_llm:
            # 控制 LLM 调用频率，避免速率限制
            await asyncio.sleep(0.1)
    logger.info(f"文件 {file_id} 实体抽取完成: {len(chunks)} chunks, {total_entities} 实体")
    return {"chunks_processed": len(chunks), "entities_found": total_entities}


def process_file_rule(file_id: int) -> dict:
    """纯规则抽取（零 LLM，同步快速）：每个 chunk 跑规则抽取并入库

    用于入库流程的同步阶段，保证基础图谱立即可用；LLM 增强由 llm_worker 后台异步补。
    """
    chunks = db.get_chunks_by_file(file_id)
    total = 0
    for c in chunks:
        entities = entity_extractor.extract_rule(c["content"])
        _store_entities_and_edges(entities, c["id"], file_id)
        total += len(entities)
    # 文件级规格关联：同文件的 connector × param 建 spec 边（跨 chunk 关联，形成规格卡）
    _build_file_spec_edges(file_id)
    logger.info(f"文件 {file_id} 规则实体抽取完成: {len(chunks)} chunks, {total} 实体")
    return {"chunks_processed": len(chunks), "entities_found": total}


# 邻接窗口：connector 与 param 的 chunk_index 距离在此范围内才建 spec 边
SPEC_WINDOW = 100


def _build_file_spec_edges(file_id: int, window: int = SPEC_WINDOW) -> int:
    """邻接窗口规格关联：connector 与 param 仅在文档位置接近时才建 spec 边

    v2（精度修复）：
    - v1 按文件聚合，把全文件所有 param 挂到所有 connector，导致误导
      （如 M12 被错误挂上"额定电压 380V"这种泛泛出现的参数）
    - v2 改用 chunk_index 邻接窗口：只有当 param 与 connector 某个出现位置的
      chunk_index 距离 ≤ window 时才建边，模拟"参数描述的是附近的连接器"。

    返回新建边数量。
    """
    conn = db._get_conn()

    # 每个 connector 实体出现过的 chunk_index 集合（取最近邻）
    connectors = conn.execute(
        """
        SELECT DISTINCT e.id, e.name FROM entity_files ef
        JOIN entities e ON e.id = ef.entity_id
        WHERE ef.file_id=? AND e.type='connector'
        """,
        (file_id,),
    ).fetchall()
    params = conn.execute(
        """
        SELECT DISTINCT e.id, e.name FROM entity_files ef
        JOIN entities e ON e.id = ef.entity_id
        WHERE ef.file_id=? AND e.type='param'
        """,
        (file_id,),
    ).fetchall()
    if not connectors or not params:
        return 0

    # 预取所有 connector/param 的 chunk_index 位置（只算同文件）
    def _positions(entity_ids, etype):
        ids = tuple(entity_ids)
        if not ids:
            return {}
        q = f"""
            SELECT ec.entity_id, ch.chunk_index FROM entity_chunks ec
            JOIN chunks ch ON ch.id = ec.chunk_id
            JOIN entities e ON e.id = ec.entity_id
            WHERE ch.file_id=? AND e.type=? AND ec.entity_id IN ({','.join('?' * len(ids))})
        """
        pos = {}
        for r in conn.execute(q, (file_id, etype, *ids)).fetchall():
            pos.setdefault(r["entity_id"], []).append(r["chunk_index"])
        return pos

    conn_pos = _positions([c["id"] for c in connectors], "connector")
    param_pos = _positions([p["id"] for p in params], "param")

    stale = {r["source_id"]: r["target_id"] for r in conn.execute(
        "SELECT source_id, target_id FROM entity_relations WHERE rel_type='spec'").fetchall()}

    count = 0
    for c in connectors:
        cpos = conn_pos.get(c["id"], [])
        if not cpos:
            continue
        for p in params:
            ppos = param_pos.get(p["id"], [])
            if not ppos:
                continue
            # 最小 chunk_index 距离
            min_dist = min(abs(ci - pi) for ci in cpos for pi in ppos)
            if min_dist <= window:
                db.add_entity_relation(c["id"], p["id"], rel_type="spec", weight=1.0)
                count += 1
    if count:
        logger.info(f"文件 {file_id} 生成 {count} 条 spec 规格边（邻接窗口≤{window}）")
    return count


def build_semantic_edges() -> dict:
    """语义关系边构建（任务 B）：把无差别的 cooccur 升级为领域语义关系

    三类语义边：
    1. compatible_process：material → process（材料-工艺相容性知识表驱动）
       例：不锈钢 → 热处理、黄铜 → 电镀
    2. standard_category：standard → 领域类别（标准号前缀规则驱动）
       例：GB/T 4458 → 机械制图
    3. uses_standard：material → standard（同句共现驱动，精确到句中）
       例：不锈钢 → GB/T 1220（当两者在同一句中出现）

    返回 {compatible_process, standard_category, uses_standard} 各边数
    """
    conn = db._get_conn()
    result = {"compatible_process": 0, "standard_category": 0, "uses_standard": 0}

    # 1. material → process（知识表）
    materials = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM entities WHERE type='material'").fetchall()
    }
    processes = {
        r["name"]: r["id"]
        for r in conn.execute("SELECT id, name FROM entities WHERE type='process'").fetchall()
    }
    for mid, mname in materials.items():
        for proc in entity_extractor.MATERIAL_PROCESS_MAP.get(mname, []):
            if proc in processes:
                db.add_entity_relation(mid, processes[proc], rel_type="compatible_process", weight=1.0)
                result["compatible_process"] += 1

    # 2. standard → category（前缀规则）
    standards = conn.execute(
        "SELECT id, name FROM entities WHERE type='standard'").fetchall()
    # 标准分类：缓存优先 > 规则兜底 > LLM 精分类（低置信度号段）
    need_llm = []  # 需要 LLM 精分类的（低置信且无缓存）
    for s in standards:
        name = s["name"]
        # 1. 查缓存（LLM 精分类结果优先）
        cached = db.get_standard_category(name)
        if cached:
            cat = cached
        else:
            cat = entity_extractor.classify_standard(name)
            # 低置信度号段 → 记录，稍后批量 LLM 精分类
            if entity_extractor.is_low_confidence_standard(name):
                need_llm.append(name)
        if cat:
            conn.execute(
                "UPDATE entities SET attributes = json_set(COALESCE(attributes,'{}'), '$.standard_domain', ?) WHERE id=?",
                (cat, s["id"]),
            )
            result["standard_category"] += 1
        else:
            # 未命中前缀规则 → 归「其他」（保证每个标准实体都有标准字段）
            conn.execute(
                "UPDATE entities SET attributes = json_set(COALESCE(attributes,'{}'), '$.standard_domain', '其他') WHERE id=?",
                (s["id"],),
            )
    conn.commit()

    # LLM 批量精分类（低置信度号段，写缓存 + 更新 attributes）
    if need_llm:
        llm_result = entity_extractor.llm_classify_standards(need_llm)
        for name, cat in llm_result.items():
            db.set_standard_category(name, cat, source="llm")
            conn.execute(
                "UPDATE entities SET attributes = json_set(COALESCE(attributes,'{}'), '$.standard_domain', ?) WHERE name=? AND type='standard'",
                (cat, name),
            )
        conn.commit()
        logger.info(f"LLM 精分类 {len(llm_result)}/{len(need_llm)} 个低置信度标准号")

    # 3. material → standard（同句共现）
    result["uses_standard"] = _build_uses_standard_edges()
    return result


def _build_uses_standard_edges() -> int:
    """严格同句共现：material 与 standard 在原文同一句内出现才建 uses_standard 边

    v2（精度修复）：v1 用 chunk 级共现，把材料所在 chunk 的所有标准号都连上
    （如不锈钢连了 47 个标准，因为密度表、紧固件表挤在同一个 chunk），误导。
    v2 改为按句切分 chunk content，只有当 material 名和标准号在同一个句子里
    才判定为"材料→采用标准"。

    返回新边数量。
    """
    conn = db._get_conn()
    # 候选：同一 chunk 内的 material-standard 对（先粗筛，再逐句精筛）
    candidates = conn.execute(
        """
        SELECT DISTINCT a.id AS mid, a.name AS mname, b.id AS sid, b.name AS sname, ch.id AS chunk_id
        FROM entity_chunks ec
        JOIN entities a ON a.id = ec.entity_id AND a.type='material'
        JOIN entity_chunks ec2 ON ec2.chunk_id = ec.chunk_id
        JOIN entities b ON b.id = ec2.entity_id AND b.type='standard'
        JOIN chunks ch ON ch.id = ec.chunk_id
        WHERE a.id != b.id
        """,
    ).fetchall()

    # 预取每个相关 chunk 的 content（避免重复查询）
    chunk_ids = {r["chunk_id"] for r in candidates}
    chunk_content = {}
    if chunk_ids:
        q = f"SELECT id, content FROM chunks WHERE id IN ({','.join('?' * len(chunk_ids))})"
        for r in conn.execute(q, tuple(chunk_ids)).fetchall():
            chunk_content[r["id"]] = r["content"] or ""

    # 句切分：中文句号/分号/换行等
    SENT_SPLIT = re.compile(r'[。；;\n\r]+')

    count = 0
    seen = set()
    existing = {
        (r["source_id"], r["target_id"])
        for r in conn.execute(
            "SELECT source_id, target_id FROM entity_relations WHERE rel_type='uses_standard'").fetchall()
    }
    for p in candidates:
        key = (p["mid"], p["sid"])
        if key in seen or key in existing:
            continue
        content = chunk_content.get(p["chunk_id"], "")
        mname = p["mname"]
        sname = p["sname"]
        # 逐句判断两者是否同句
        co_sentence = False
        for sent in SENT_SPLIT.split(content):
            if mname in sent and sname in sent:
                co_sentence = True
                break
        if co_sentence:
            db.add_entity_relation(p["mid"], p["sid"], rel_type="uses_standard", weight=1.0)
            count += 1
            seen.add(key)
    return count


def build_document_similarity_edges(top_k: int = 5, threshold: float = 0.7, file_id: int = None) -> int:
    """文档相似度建边：基于文件向量相似度（阶段 2 增强）

    file_id 提供时：仅计算该文件与其余文件的相似度（增量，适用于入库后），
    避免每次全量 O(n²) 重算。file_id 为 None 时全量两两计算（首次/重建）。

    返回新建边数量。
    """
    conn = db._get_conn()
    files = conn.execute("SELECT id FROM files").fetchall()
    if not files:
        return 0
    import numpy as np

    def _file_vector(fid):
        rows = conn.execute(
            "SELECT embedding FROM chunks WHERE file_id=? AND embedding IS NOT NULL", (fid,)
        ).fetchall()
        if not rows:
            return None
        from src.pipeline.embedder import _unpack
        vecs = [_unpack(r["embedding"]) for r in rows]
        return np.mean(vecs, axis=0)

    count = 0
    all_ids = [f["id"] for f in files]
    if file_id is not None and file_id in all_ids:
        # 增量：新文件 vs 其余已有文件
        target_ids = [file_id]
        other_ids = [i for i in all_ids if i != file_id]
    else:
        target_ids = all_ids
        other_ids = all_ids

    # 预先卸载向量，避免重复计算
    vec_cache = {}
    def _v(fid):
        if fid not in vec_cache:
            vec_cache[fid] = _file_vector(fid)
        return vec_cache[fid]

    for i in target_ids:
        a = _v(i)
        if a is None:
            continue
        for j in other_ids:
            if j <= i:
                continue
            b = _v(j)
            if b is None:
                continue
            sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
            if sim >= threshold:
                db.add_link(i, j, link_type="similar", weight=sim, context=f"similarity={sim:.3f}")
                count += 1
    return count
