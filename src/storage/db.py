"""
存储层 — 统一入口
================
连接管理、Schema、迁移已拆至 connection.py（零循环依赖）。
本模块仅做 re-export，让外部 `from src.storage.db import xxx` 照常工作。
"""
# 连接管理（从 connection.py 转发）
from src.storage.connection import (
    _new_conn, _get_conn, get_connection, set_conn, reset_conn, close_thread_conn,
    init_db, _dumps, SCHEMA,
)

# 文件/文件夹/图片
from src.storage.files import (
    add_file, get_file, list_files, list_files_with_entities,
    update_file_category, update_file_tags, update_file_summary, update_file_folder,
    update_file_doc_meta, get_file_authority,
    normalize_folder, list_folders, delete_file, sync_chunk_count,
    add_images, list_images, count_images,
)
# Chunk + FTS5 搜索 + 链接
from src.storage.chunks import (
    add_chunks_batch, get_chunks_by_file, fts_search, add_link, get_graph_data, get_file_backlinks,
    reconcile_fts,
)
# 实体/关系/图谱
from src.storage.entities import (
    upsert_entity, add_entity_chunk, add_entity_file, add_entity_relation,
    list_entities, get_entity, get_entity_by_name,
    get_entity_chunks, get_entity_files,
    get_standard_category, set_standard_category,
    get_entity_spec_params, get_entity_relations, get_entity_graph,
    get_entity_local_graph, get_entity_degree,
)
# 对话会话
from src.storage.conversations import (
    create_conversation, list_conversations, get_conversation,
    get_conversation_messages, add_conversation_message,
    update_conversation_title, delete_conversation,
    get_chunk_references, get_chunk_ref_counts,
)
# 入库任务持久化
from src.storage.tasks import (
    save_task, load_tasks, mark_stale_tasks_failed, delete_task,
    load_retryable_tasks, load_resumable_tasks, mark_task_retrying,
    mark_task_dead, save_checkpoint, get_dead_letter_tasks, retry_dead_letter,
    cancel_task, cleanup_stale_tasks,
)
from src.storage.wiki import (
    create_page as create_wiki_page, get_page as get_wiki_page,
    get_page_by_slug as get_wiki_page_by_slug, list_pages as list_wiki_pages,
    update_page as update_wiki_page, delete_page as delete_wiki_page,
    add_link as add_wiki_link, get_links as get_wiki_links,
    get_backlinks as get_wiki_backlinks, get_versions as get_wiki_versions,
    get_stale_pages as get_wiki_stale_pages,
)
