"""
wiki_compiler.py — Wiki 页面编译 Stage（02 文档 M2）

把高价值文档由 LLM 编译成结构化 Markdown 知识页。
复用现有 call_llm_sync + extract_json，失败降级跳过（Stage 非 critical）。

触发入口：
  A. 入库后自动编译（新增 async stage "compile_wiki"）
  B. 人工触发（/api/wiki/compile）
  C. 批量回填（scripts/compile_wiki_backfill.py）
"""
import json
import logging

logger = logging.getLogger("rag.wiki.compiler")

# 编译条件：满足任一则编译
_COMPILE_KINDS = {"技术手册", "规格书", "标准", "手册"}
_COMPILE_AUTHORITY_MIN = 4  # 权威等级 ≥ 4


def _should_compile(file_id: int, text: str) -> bool:
    """判断是否应该编译为 Wiki 页面"""
    if not text or len(text.strip()) < 200:
        return False
    try:
        from src.storage.db import get_file
        f = get_file(file_id)
        if not f:
            return False
        # 条件 1：文档类型
        kind = f.get("doc_kind", "")
        if kind in _COMPILE_KINDS:
            return True
        # 条件 2：权威等级
        if f.get("authority", 0) >= _COMPILE_AUTHORITY_MIN:
            return True
        return False
    except Exception:
        return False


def compile_page(file_id: int, text: str = None) -> dict | None:
    """编译单个文档为 Wiki 页面，返回 {slug, title, content_md, ...} 或 None。

    同步调用（用于后台线程），失败返回 None。
    """
    if not text:
        try:
            from src.storage.db import get_file
            f = get_file(file_id)
            if not f:
                return None
            # 读取文档全文（从 chunks 拼接）
            from src.storage.db import get_chunks_by_file
            chunks = get_chunks_by_file(file_id)
            if not chunks:
                return None
            text = "\n\n".join(c["content"] for c in chunks[:50])  # 取前 50 块
        except Exception as e:
            logger.warning(f"编译准备失败: {e}")
            return None

    if not _should_compile(file_id, text):
        return None

    # 构造编译 prompt
    prompt = _build_compile_prompt(text)
    try:
        from src.llm_client import call_llm_sync
        result = call_llm_sync([{"role": "user", "content": prompt}], max_tokens=2000, prefer_mimo=True)
        if not result:
            logger.warning(f"LLM 编译返回空: file_id={file_id}")
            return None
        # 解析结构化输出
        meta = _extract_compile_result(result)
        if meta:
            meta["source_file_ids"] = [file_id]
            logger.info(f"Wiki 编译成功: {meta.get('title', '?')} <- file_id={file_id}")
            return meta
    except Exception as e:
        logger.warning(f"编译异常（已忽略）: {e}")
    return None


def _build_compile_prompt(text: str) -> str:
    """构造编译 prompt（结构化输出契约）"""
    return f"""你是工业知识库的"知识编译员"。把给定的文档资料编译成一篇结构化 Markdown 知识页。

输出 JSON（不要输出其他内容）：
{{
  "title": "页面标题（概念名）",
  "category": "从以下选一个：连接器, 材料选型, 工艺规程, 机械设计, 标准件, 品质管理, 电气自动化, 操作手册, 未分类",
  "summary": "一句话摘要（≤50字）",
  "content_md": "Markdown 正文，结构：## 定义 / ## 关键参数（表格）/ ## 选型要点 / ## 相关标准 / ## 注意事项。只写资料中真实存在的内容，不编造数值",
  "entities": ["资料中出现的关键实体名（连接器型号/材料/标准号）"],
  "related_topics": ["建议关联的其它主题名"]
}}

约束：数值/型号/标准号必须原文抄录，不得改写。内容超长时优先保留参数表和结论。

文档资料：
{text[:8000]}"""


def _extract_compile_result(llm_output: str) -> dict | None:
    """从 LLM 输出中提取 JSON"""
    try:
        from src.llm_client import extract_json
        data = extract_json(llm_output)
        if data and data.get("title") and data.get("content_md"):
            return data
    except Exception:
        pass
    # fallback: 直接 json.loads
    try:
        # 尝试提取 JSON 块
        import re
        m = re.search(r'\{[\s\S]*\}', llm_output)
        if m:
            data = json.loads(m.group())
            if data.get("title") and data.get("content_md"):
                return data
    except Exception:
        pass
    return None


def save_compiled_page(meta: dict) -> int | None:
    """把编译结果写入 wiki_pages，返回 page_id"""
    try:
        from src.storage.db import create_wiki_page
        page_id = create_wiki_page(
            title=meta["title"],
            content_md=meta["content_md"],
            category=meta.get("category", "未分类"),
            summary=meta.get("summary", ""),
            source_file_ids=meta.get("source_file_ids", []),
            entity_ids=[],
            compiled_by="llm",
        )
        # 关联实体（如果有）
        entities = meta.get("entities", [])
        if entities:
            _link_entities(page_id, entities)
        return page_id
    except Exception as e:
        logger.warning(f"保存编译结果失败: {e}")
        return None


def _link_entities(page_id: int, entity_names: list[str]):
    """把实体名关联到 Wiki 页面（通过 entity_ids）"""
    try:
        from src.storage.db import _get_conn
        conn = _get_conn()
        entity_ids = []
        for name in entity_names:
            row = conn.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
            if row:
                entity_ids.append(row["id"])
        if entity_ids:
            conn.execute(
                "UPDATE wiki_pages SET entity_ids=? WHERE id=?",
                (json.dumps(entity_ids), page_id)
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"实体关联失败（已忽略）: {e}")
