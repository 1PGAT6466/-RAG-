"""api/graph.py — 知识图谱 + 实体路由"""
from fastapi import APIRouter, HTTPException, Depends

from src.auth.deps import get_current_user
from src.storage.db import (
    get_graph_data, list_entities, get_entity,
    get_entity_chunks, get_entity_files,
    get_entity_spec_params, get_entity_relations, get_entity_graph,
    get_entity_local_graph, get_entity_degree,
)

router = APIRouter()


@router.get("/api/graph")
def api_graph(user=Depends(get_current_user)):
    data = get_graph_data()
    return {"status": "ok", "data": data}


@router.get("/api/entities")
def api_entities(etype: str = None, user=Depends(get_current_user)):
    entities = list_entities(etype=etype)
    return {"status": "ok", "data": entities}


@router.get("/api/entities/graph")
def api_entity_graph(etype: str = None, user=Depends(get_current_user)):
    types = [t.strip() for t in etype.split(",") if t.strip()] if etype else None
    data = get_entity_graph(types=types)
    return {"status": "ok", "data": data}


@router.get("/api/entities/{entity_id}/graph")
def api_entity_local_graph(entity_id: int, hops: int = 2, max_nodes: int = 200, user=Depends(get_current_user)):
    """局部图谱：从某实体出发取 N 跳邻域（Obsidian 式聚焦展开）"""
    entity = get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    data = get_entity_local_graph(entity_id, hops=hops, max_nodes=max_nodes)
    return {"status": "ok", "data": data}


@router.get("/api/entities/{entity_id}")
def api_entity_detail(entity_id: int, user=Depends(get_current_user)):
    entity = get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    chunks = get_entity_chunks(entity_id)
    files = get_entity_files(entity_id)
    spec_params = get_entity_spec_params(entity_id)
    relations = get_entity_relations(entity_id)
    # 补入度/出度/总度（用于反链面板展示连接方向）
    try:
        deg = get_entity_degree(entity_id)
    except Exception:
        deg = None
    if deg:
        entity["degree"] = deg["degree"]
        entity["in_degree"] = deg["in_degree"]
        entity["out_degree"] = deg["out_degree"]
    return {"status": "ok", "data": {
        "entity": entity, "chunks": chunks, "files": files,
        "spec_params": spec_params, "relations": relations,
    }}
