"""实体/关系/图谱操作"""
import json
import logging

from src.storage.connection import _get_conn

logger = logging.getLogger('rag.db.entities')


def upsert_entity(name: str, etype: str = "unknown", aliases: list = None,
                  description: str = "", attributes: dict = None) -> int:
    """插入或更新实体，返回实体 id（ON CONFLICT 消除并发竞态）"""
    conn = _get_conn()
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO entities (name, type, aliases, description, attributes)
           VALUES (?,?,?,?,?)
           ON CONFLICT(name, type) DO UPDATE SET
               aliases=excluded.aliases,
               description=excluded.description,
               attributes=excluded.attributes""",
        (name, etype, aliases_json, description, attrs_json),
    )
    conn.commit()
    # ON CONFLICT UPDATE 时 lastrowid 可能为 0，需补查
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM entities WHERE name=? AND type=?", (name, etype)
    ).fetchone()
    return row["id"] if row else 0


def add_entity_chunk(entity_id: int, chunk_id: int, mention_count: int = 1) -> None:
    """记录实体-分块关联（累加 mention_count）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_chunks (entity_id, chunk_id, mention_count)
        VALUES (?,?,?)
        ON CONFLICT(entity_id, chunk_id) DO UPDATE SET
            mention_count = entity_chunks.mention_count + excluded.mention_count
        """,
        (entity_id, chunk_id, mention_count),
    )
    conn.commit()


def add_entity_file(entity_id: int, file_id: int, mention_count: int = 1) -> None:
    """记录实体-文件关联（累加）"""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_files (entity_id, file_id, mention_count)
        VALUES (?,?,?)
        ON CONFLICT(entity_id, file_id) DO UPDATE SET
            mention_count = entity_files.mention_count + excluded.mention_count
        """,
        (entity_id, file_id, mention_count),
    )
    conn.commit()


def add_entity_relation(source_id: int, target_id: int, rel_type: str = "cooccur",
                        weight: float = 1.0, context: str = "") -> None:
    """实体间关系（重复则累加权重）"""
    if source_id == target_id:
        return
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO entity_relations (source_id, target_id, rel_type, weight, context)
        VALUES (?,?,?,?,?)
        ON CONFLICT(source_id, target_id, rel_type) DO UPDATE SET
            weight = entity_relations.weight + excluded.weight
        """,
        (source_id, target_id, rel_type, weight, context),
    )
    conn.commit()


def list_entities(etype: str = None, limit: int = 500) -> list[dict]:
    """实体列表，可按 type 过滤"""
    conn = _get_conn()
    if etype:
        rows = conn.execute(
            "SELECT * FROM entities WHERE type=? ORDER BY id", (etype,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entities ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_entity(entity_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def get_entity_degree(entity_id: int) -> dict:
    """单个实体的总度/入度/出度（有向：out=作为 source，in=作为 target）。"""
    conn = _get_conn()
    out_deg = conn.execute(
        "SELECT COUNT(*) FROM entity_relations WHERE source_id=?", (entity_id,)
    ).fetchone()[0]
    in_deg = conn.execute(
        "SELECT COUNT(*) FROM entity_relations WHERE target_id=?", (entity_id,)
    ).fetchone()[0]
    return {
        "degree": out_deg + in_deg,
        "in_degree": in_deg,
        "out_degree": out_deg,
    }


def get_entity_by_name(name: str, etype: str = None) -> dict | None:
    conn = _get_conn()
    if etype:
        row = conn.execute("SELECT * FROM entities WHERE name=? AND type=?", (name, etype)).fetchone()
    else:
        row = conn.execute("SELECT * FROM entities WHERE name=? LIMIT 1", (name,)).fetchone()
    return dict(row) if row else None


def get_entity_chunks(entity_id: int) -> list[dict]:
    """反链：实体被哪些 chunk 提到（排除已删除文件）"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT ec.chunk_id, ec.mention_count, c.content, c.file_id, c.chunk_index, f.name as file_name
        FROM entity_chunks ec
        JOIN chunks c ON c.id = ec.chunk_id
        JOIN files f ON f.id = c.file_id AND f.deleted_at IS NULL
        WHERE ec.entity_id=? ORDER BY ec.mention_count DESC
        """,
        (entity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_files(entity_id: int) -> list[dict]:
    """反链：实体出现在哪些文件（排除已删除文件）"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT ef.file_id, ef.mention_count, f.name as file_name, f.category
        FROM entity_files ef
        JOIN files f ON f.id = ef.file_id AND f.deleted_at IS NULL
        WHERE ef.entity_id=? ORDER BY ef.mention_count DESC
        """,
        (entity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_standard_category(name: str) -> str | None:
    """查询标准号分类缓存"""
    conn = _get_conn()
    row = conn.execute("SELECT category FROM standard_categories WHERE name=?", (name,)).fetchone()
    return row["category"] if row else None


def set_standard_category(name: str, category: str, source: str = "llm") -> None:
    """写入标准号分类缓存（upsert）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO standard_categories (name, category, source, updated_at)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(name) DO UPDATE SET category=excluded.category, source=excluded.source, updated_at=datetime('now')""",
        (name, category, source),
    )
    conn.commit()


def get_entity_spec_params(entity_id: int) -> list[dict]:
    """实体的规格参数（spec 边关联的 param 实体），用于连接器规格卡"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT e.id, e.name, e.attributes, er.weight
        FROM entity_relations er
        JOIN entities e ON e.id = CASE
            WHEN er.source_id=? THEN er.target_id
            ELSE er.source_id
        END
        WHERE er.rel_type='spec' AND (er.source_id=? OR er.target_id=?)
          AND e.type='param'
        ORDER BY er.weight DESC
        """,
        (entity_id, entity_id, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_relations(entity_id: int, rel_types: list[str] | None = None) -> list[dict]:
    """实体的语义关系（排除 cooccur），用于实体详情面板展示

    返回该实体作为 source 或 target 的所有非 cooccur 关系，带方向标注。
    rel_types: 可选，只返回指定关系类型。
    """
    conn = _get_conn()
    if rel_types:
        placeholders = ",".join("?" * len(rel_types))
        rows = conn.execute(
            f"""
            SELECT er.source_id, er.target_id, er.rel_type, er.weight, er.context,
                   s.name AS source_name, s.type AS source_type,
                   t.name AS target_name, t.type AS target_type
            FROM entity_relations er
            JOIN entities s ON s.id = er.source_id
            JOIN entities t ON t.id = er.target_id
            WHERE er.rel_type IN ({placeholders})
              AND (er.source_id=? OR er.target_id=?)
            ORDER BY er.weight DESC
            """,
            (*rel_types, entity_id, entity_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT er.source_id, er.target_id, er.rel_type, er.weight, er.context,
                   s.name AS source_name, s.type AS source_type,
                   t.name AS target_name, t.type AS target_type
            FROM entity_relations er
            JOIN entities s ON s.id = er.source_id
            JOIN entities t ON t.id = er.target_id
            WHERE er.rel_type != 'cooccur'
              AND (er.source_id=? OR er.target_id=?)
            ORDER BY er.weight DESC
            """,
            (entity_id, entity_id),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 标注方向：当前实体是 source(target→source) 还是 target(source→target)
        if r["source_id"] == entity_id:
            d["direction"] = "out"
            d["other_name"] = r["target_name"]
            d["other_type"] = r["target_type"]
        else:
            d["direction"] = "in"
            d["other_name"] = r["source_name"]
            d["other_type"] = r["source_type"]
        result.append(d)
    return result


def get_entity_graph(types: list[str] | None = None) -> dict:
    """返回实体关系图：实体节点 + 关系边。

    节点带：
      - degree（总度数，用于前端渲染大小）
      - in_degree / out_degree（有向入度/出度：out = 作为 source 指出去，in = 作为 target 被指向）
      - created_at（入库时间，供 Timeline 时间轴视图）

    types: 可选，按实体类型过滤（只保留含至少一个指定类型的边）
    """
    conn = _get_conn()
    if types:
        placeholders = ",".join("?" * len(types))
        nodes = conn.execute(
            f"SELECT id, name, type, attributes, created_at, CAST(id AS TEXT) as group_id FROM entities WHERE type IN ({placeholders})",
            tuple(types),
        ).fetchall()
    else:
        nodes = conn.execute(
            "SELECT id, name, type, attributes, created_at, CAST(id AS TEXT) as group_id FROM entities"
        ).fetchall()
    node_list = [dict(n) for n in nodes]
    node_ids = {n["id"] for n in node_list}
    all_edges = conn.execute(
        "SELECT source_id, target_id, rel_type, weight, context FROM entity_relations"
    ).fetchall()
    # 只保留两端节点都在过滤结果里的边
    edge_list = [
        dict(e) for e in all_edges
        if e["source_id"] in node_ids and e["target_id"] in node_ids
    ]
    # 计算节点度数：总度数 + 有向入度/出度拆分
    degree = {}
    in_deg = {}
    out_deg = {}
    for e in edge_list:
        degree[e["source_id"]] = degree.get(e["source_id"], 0) + 1
        degree[e["target_id"]] = degree.get(e["target_id"], 0) + 1
        out_deg[e["source_id"]] = out_deg.get(e["source_id"], 0) + 1
        in_deg[e["target_id"]] = in_deg.get(e["target_id"], 0) + 1
    for n in node_list:
        n["degree"] = degree.get(n["id"], 0)
        n["in_degree"] = in_deg.get(n["id"], 0)
        n["out_degree"] = out_deg.get(n["id"], 0)
    return {
        "nodes": node_list,
        "edges": edge_list,
    }


def get_entity_local_graph(entity_id: int, hops: int = 2, max_nodes: int = 200) -> dict:
    """局部图谱（Local Graph）：从某实体出发，BFS 取 N 跳邻域。

    返回与全图同构的 {nodes, edges}，且节点带 in_degree/out_degree（相对邻域内）。
    用于前端「展开局部图谱」：在全图里点某一实体，聚焦看它的连通上下文（Obsidian 式）。

    上限保护：图谱高度稠密时，BFS 邻域可能爆到几百节点，导致前端力导向卡顿。
    邻域超过 max_nodes 时按「中心 + 非 cooccur 语义关系优先 + 度数降序」截断。
    """
    conn = _get_conn()
    hops = max(1, min(int(hops), 5))  # 限制 1~5 跳，防大爆炸
    frontier = {entity_id}
    seen = {entity_id}
    edge_rows: list[dict] = []

    for _ in range(hops):
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"""
            SELECT source_id, target_id, rel_type, weight, context
            FROM entity_relations
            WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
            """,
            (*frontier, *frontier),
        ).fetchall()
        loop_edges = [dict(r) for r in rows]
        if not loop_edges:
            break
        edge_rows.extend(loop_edges)
        nxt = set()
        for e in loop_edges:
            nxt.add(e["source_id"])
            nxt.add(e["target_id"])
        frontier = nxt - seen
        seen |= nxt

    # 去重边（同一条边可能被两侧 frontier 重复扫到）
    dedup = {}
    for e in edge_rows:
        key = (min(e["source_id"], e["target_id"]),
               max(e["source_id"], e["target_id"]),
               e["rel_type"])
        if key not in dedup:
            dedup[key] = e
    edge_list = list(dedup.values())

    # 取邻域内所有节点
    node_ids = set()
    for e in edge_list:
        node_ids.add(e["source_id"])
        node_ids.add(e["target_id"])
    if not node_ids:
        # 无邻居：至少返回中心节点本身
        node_ids = {entity_id}

    # 邻域内入度/出度/总度（先全量算，用于截断排序）
    in_deg, out_deg, degree = {}, {}, {}
    for e in edge_list:
        degree[e["source_id"]] = degree.get(e["source_id"], 0) + 1
        degree[e["target_id"]] = degree.get(e["target_id"], 0) + 1
        in_deg[e["target_id"]] = in_deg.get(e["target_id"], 0) + 1
        out_deg[e["source_id"]] = out_deg.get(e["source_id"], 0) + 1

    # 上限保护：节点数超限时截断。中心节点始终保留。
    total_neighborhood = len(node_ids)
    truncated = total_neighborhood > max_nodes
    if truncated:
        # 排序权重：中心 > 有语义关系（非 cooccur）> 度数高
        def _rank(nid):
            hub_bonus = 0 if nid == entity_id else 0
            sem_edges = sum(
                1 for e in edge_list
                if e["rel_type"] != "cooccur"
                and (e["source_id"] == nid or e["target_id"] == nid)
            )
            # 中心节点置顶，其余按「语义边数*100 + 度数」排序
            return (hub_bonus if nid == entity_id else -1, sem_edges * 100 + degree.get(nid, 0))
        keep = {entity_id}
        others = sorted(node_ids - {entity_id}, key=_rank, reverse=True)
        for nid in others[:max_nodes - 1]:
            keep.add(nid)
        # 边只保留两端都在保留集内
        edge_list = [
            e for e in edge_list
            if e["source_id"] in keep and e["target_id"] in keep
        ]
        node_ids = keep

    placeholders = ",".join("?" * len(node_ids))
    node_rows = conn.execute(
        f"SELECT id, name, type, attributes, created_at, CAST(id AS TEXT) as group_id FROM entities WHERE id IN ({placeholders})",
        tuple(node_ids),
    ).fetchall()
    node_list = [dict(n) for n in node_rows]

    # 最终入度/出度（相对截断后的邻域）
    in_deg, out_deg, degree = {}, {}, {}
    for e in edge_list:
        degree[e["source_id"]] = degree.get(e["source_id"], 0) + 1
        degree[e["target_id"]] = degree.get(e["target_id"], 0) + 1
        out_deg[e["source_id"]] = out_deg.get(e["source_id"], 0) + 1
        in_deg[e["target_id"]] = in_deg.get(e["target_id"], 0) + 1
    for n in node_list:
        n["degree"] = degree.get(n["id"], 0)
        n["in_degree"] = in_deg.get(n["id"], 0)
        n["out_degree"] = out_deg.get(n["id"], 0)

    return {
        "nodes": node_list,
        "edges": edge_list,
        "center_id": entity_id,
        "truncated": truncated,
        "total_nodes": total_neighborhood,
    }
