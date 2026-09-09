"""
ingest_stages.py — 引擎内置 Stage 实现

把入库流水线拆成可独立降级的 Stage，注册进 engine。
每个 Stage 接收 ctx（共享上下文），产出写入 ctx。

同步 Stage（零 LLM，快速）：
  parse      解析（pdfplumber 逐页进度）
  chunk      分块
  embed      向量化
  store      入库（sqlite + FTS + ChromaDB）
  classify   自动分类
  extract    规则实体抽取 + 图谱 + 语义边（LLM 精分类异步补）

异步 Stage（LLM 密集，后台执行）：
  summarize  文档摘要
  tag        主题标签
  preindex   预设问题预索引
"""
import logging
import os
from config import UPLOAD_DIR, RAG_ENTITY_EXTRACT, RAG_ENTITY_LLM, RAG_AUTO_SUMMARY, RAG_AUTO_TAG, RAG_AUTO_PREINDEX, RAG_AUTO_SEMANTIC, RAG_AUTO_DOC_SIM, RAG_IMAGE_EXTRACT

from . import engine

logger = logging.getLogger("rag.engine.stages")


# ============================================================
# 同步 Stage
# ============================================================
@engine.register_stage("parse", stage_type="sync")
def _stage_parse(ctx: dict):
    from .parser import parse_file
    ext = ctx["ext"]
    target = ctx["target_path"]
    # 统一走 parse_file：内部对 PDF 做乱码/扫描件检测→OCR，与流式路径一致
    text = parse_file(target)
    if not text or len(text.strip()) < 50:
        raise ValueError("文档无有效内容")
    ctx["text"] = text


@engine.register_stage("chunk", stage_type="sync")
def _stage_chunk(ctx: dict):
    from .chunker import chunk_text, clean_chunks
    chunks = chunk_text(ctx["text"], source_name=ctx["filename"])
    if not chunks:
        raise ValueError("分块后无有效内容")
    # 清洗层显式化：切块（chunk_text）与语言清洗（clean_chunks）解耦
    chunks = clean_chunks(chunks)
    ctx["chunks"] = chunks
    ctx["chunk_count"] = len(chunks)


@engine.register_stage("embed", stage_type="sync", critical=True)
def _stage_embed(ctx: dict):
    from .embedder import encode
    chunks = ctx["chunks"]
    contents = [c["content"] for c in chunks]
    total = len(contents)
    # 用任务状态上报进度（大文档向量化不再干等无反馈）
    def _on_progress(done, total_n):
        emit = ctx.get("emit")
        if emit:
            pct = int(done / total_n * 100)
            emit("embed", pct, f"向量化 {done}/{total_n} 块")
    ctx["embeddings"] = encode(contents, progress_cb=_on_progress)
    ctx["token_counts"] = [max(1, len(c) // 2) for c in contents]


@engine.register_stage("store", stage_type="sync", critical=True)
def _stage_store(ctx: dict):
    from src.storage.db import add_file, add_chunks_batch, sync_chunk_count
    chunks = ctx["chunks"]
    file_id = add_file(
        name=ctx["filename"], path=ctx["target_path"], ext=ctx["ext"],
        size=os.path.getsize(ctx["target_path"])
    )
    batch = [
        (file_id, c["index"], c["content"], ctx["token_counts"][i], ctx["embeddings"][i],
         {"heading": c["heading"], "source": c["source"], "markdown": bool(c.get("markdown"))})
        for i, c in enumerate(chunks)
    ]
    chunk_ids = add_chunks_batch(batch)
    sync_chunk_count(file_id)
    ctx["file_id"] = file_id
    ctx["chunk_ids"] = chunk_ids

    # ChromaDB（失败不阻断）
    try:
        from src.storage.chroma_store import add_batch, _use_chroma
        if _use_chroma():
            add_batch([
                (chunk_ids[i], ctx["embeddings"][i], chunks[i]["content"], file_id, i)
                for i in range(len(chunk_ids))
            ])
    except Exception as e:
        logger.warning(f"ChromaDB 写入失败（已忽略）: {e}")


@engine.register_stage("classify", stage_type="sync")
def _stage_classify(ctx: dict):
    from src.classification import classify_document, detect_document_folder, detect_doc_meta
    from src.storage.db import update_file_category, update_file_folder, update_file_doc_meta
    cat = classify_document(ctx["filename"], ctx["text"])
    ctx["category"] = cat
    file_id = ctx.get("file_id")
    if not file_id:
        return
    if cat != "未分类":
        update_file_category(file_id, cat)

    # 操作手册按发行系统自动建虚拟文件夹（如 /泛微OA），实现分系统归类
    folder = detect_document_folder(ctx["filename"], ctx["text"])
    if folder:
        update_file_folder(ctx["file_id"], folder)
        ctx["folder"] = folder

    # 元数据层：文档类型 + 权威等级（专属化检索精准度，见方案文档）
    doc_kind, authority = detect_doc_meta(ctx["filename"], cat, ctx["text"])
    update_file_doc_meta(file_id, doc_kind, authority)
    ctx["doc_kind"] = doc_kind
    ctx["authority"] = authority


@engine.register_stage("extract", stage_type="sync")
def _stage_extract(ctx: dict):
    """规则实体抽取（零 LLM，快速），LLM 增强异步补"""
    if RAG_ENTITY_EXTRACT != "1":
        return
    file_id = ctx["file_id"]
    from src.extraction.relation_builder import process_file_rule
    try:
        process_file_rule(file_id)
    except Exception as e:
        logger.warning(f"规则实体抽取失败（已忽略）: {e}")
        return
    # LLM 精分类异步补（标准号低置信度）
    if RAG_ENTITY_LLM == "1":
        try:
            from src.extraction.llm_worker import submit_file
            submit_file(file_id)
        except Exception as e:
            logger.warning(f"LLM 实体增强提交失败（已忽略）: {e}")


# ============================================================
# 异步 Stage（LLM 密集，后台执行）
# ============================================================
def _llm_chat(text: str, system: str, max_tokens: int = 1024, prefer_deepseek: bool = False) -> str:
    """同步 LLM 调用（供后台线程用），委托 src.llm。"""
    from src.llm import call_llm_sync
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    return call_llm_sync(messages, max_tokens=max_tokens, prefer_mimo=not prefer_deepseek)


def _doc_head(ctx: dict, limit: int = 8000) -> str:
    """取文档开头片段作为摘要/标签的输入（控制 token）"""
    text = ctx.get("text", "")
    return text[:limit]


@engine.register_stage("summarize", stage_type="async")
def _stage_summarize(ctx: dict):
    """文档摘要 + 标签 —— 合并为一次 LLM 调用（LLM 减负：原本 2 次→1 次）

    若 RAG_AUTO_TAG 开启，则一次调用产出 JSON {summary, tags}，避免再单独调 tag stage。
    """
    if RAG_AUTO_SUMMARY != "1":
        return
    head = _doc_head(ctx)

    want_tags = RAG_AUTO_TAG == "1"
    if want_tags:
        prompt = (
            '请分析以下工业文档，输出一个 JSON 对象，格式为 '
            '{"summary": "3-5句话切要说明，突出标准/材料/工艺/关键参数", '
            '"tags": ["5-8个主题标签"]}，只输出 JSON 不要其他内容：\n\n'
            f'{head}'
        )
        raw = _llm_chat(prompt, "你是工业文档摘要与打标助手，只输出一个 JSON 对象。",
                        max_tokens=1024, prefer_deepseek=True)
        import json
        summary, tags = '', []
        try:
            from src.llm import extract_json
            data = extract_json(raw, expect="object") or {}
            summary = (data.get("summary") or "").strip()
            tags = [t for t in (data.get("tags") or []) if isinstance(t, str)][:8]
        except Exception:
            summary = raw.strip()
        ctx["summary"] = summary
        ctx["tags"] = tags
        try:
            from src.storage import db
            if summary and hasattr(db, "update_file_summary"):
                db.update_file_summary(ctx["file_id"], summary)
            if tags and hasattr(db, "update_file_tags"):
                db.update_file_tags(ctx["file_id"], tags)
        except Exception:
            pass
        logger.info(f"文件 {ctx.get('file_id')} 摘要+标签合并生成完成({len(tags)} 标签)")
        return

    # tag 关闭：仅出摘要
    summary = _llm_chat(
        f"请用 3-5 句话概括以下工业文档的核心内容，突出涉及的标准、材料、工艺和关键参数：\n\n{head}",
        "你是工业文档摘要助手，输出精炼摘要。",
        max_tokens=1024,
        prefer_deepseek=True,
    )
    ctx["summary"] = summary.strip()
    # 回写文件元数据（若 db 支持 summary 字段则存，否则仅内存）
    try:
        from src.storage import db
        if hasattr(db, "update_file_summary"):
            db.update_file_summary(ctx["file_id"], summary.strip())
    except Exception:
        pass
    logger.info(f"文件 {ctx.get('file_id')} 摘要生成完成")


@engine.register_stage("tag", stage_type="async")
def _stage_tag(ctx: dict):
    if RAG_AUTO_TAG != "1":
        return
    # tags 已由 summarize 合并产出，则不再单独调 LLM（避免重复调用）
    if ctx.get("tags"):
        logger.debug(f"文件 {ctx.get('file_id')} 标签已由 summarize 合并产出，跳过")
        return
    # 兑底：summarize 关闭但 tag 开启时，单独打标
    head = _doc_head(ctx, 4000)
    raw = _llm_chat(
        f"从以下工业文档提炼 5-8 个主题标签，用 JSON 数组格式输出，不要输出其他内容：\n\n{head}",
        "你是工业知识打标助手。只输出一个 JSON 字符串数组，例如 [连接器,镀金工艺,GB/T 699]。",
        max_tokens=512,
        prefer_deepseek=True,
    )
    import json
    try:
        start, end = raw.find("["), raw.rfind("]")
        tags = json.loads(raw[start:end + 1]) if start != -1 and end != -1 else []
        tags = [t for t in tags if isinstance(t, str)][:8]
    except Exception:
        tags = []
    ctx["tags"] = tags
    try:
        from src.storage import db
        if hasattr(db, "update_file_tags"):
            db.update_file_tags(ctx["file_id"], tags)
    except Exception:
        pass
    logger.info(f"文件 {ctx.get('file_id')} 标签生成完成: {tags}")


@engine.register_stage("preindex", stage_type="async")
def _stage_preindex(ctx: dict):
    if RAG_AUTO_PREINDEX != "1":
        return
    # 按文档类型条件触发：操作手册/OA 办公文档不预生成问答（它们偏流程指引，
    # 预设的「标准/材料/工艺/参数」四问对 OA 手册基本无效，纯浪费 LLM 调用）。
    # 仅工业技术文档（连接器/材料/标准/工艺等）才 preindex。
    cat = ctx.get("category") or ""
    if cat == "操作手册":
        logger.info(f"文件 {ctx.get('file_id')} 为操作手册（{cat}），跳过 preindex")
        return
    head = _doc_head(ctx, 6000)
    questions = [
        "这份文档涉及哪些国家/行业标准？列举标准号及其用途",
        "这份文档提到哪些材料？各自的性能特点和适用场景",
        "这份文档涉及哪些关键工艺？流程要点是什么",
        "这份文档有哪些关键参数指标",
    ]
    answers = {}
    for q in questions:
        try:
            answers[q] = _llm_chat(
                f"基于以下文档回答问题，简明扼要：\n\n文档：{head}\n\n问题：{q}",
                "你是工业知识库助手，基于文档回答。",
                max_tokens=512,
                prefer_deepseek=True,
            ).strip()
        except Exception as e:
            logger.warning(f"预索引问题失败: {e}")
            answers[q] = ""
    ctx["preindex"] = answers
    logger.info(f"文件 {ctx.get('file_id')} 预索引完成（{len(answers)} 问）")


@engine.register_stage("semantic", stage_type="async")
def _stage_semantic(ctx: dict):
    """语义边构建：compatible_process + 标准字段（standard_domain）+ uses_standard

    幂等：全库扫描 material/standard 实体重算语义边，入库后后台跑，失败不阻断。
    """
    if RAG_AUTO_SEMANTIC != "1":
        return
    try:
        from src.extraction.relation_builder import build_semantic_edges
        result = build_semantic_edges()
        logger.info(f"文件 {ctx.get('file_id')} 语义边构建完成: {result}")
    except Exception as e:
        logger.warning(f"语义边构建失败（已忽略）: {e}")


@engine.register_stage("docsim", stage_type="async")
def _stage_docsim(ctx: dict):
    """文档相似度边构建（similar）：增量计算当前文件与其余文件的相似度"""
    if RAG_AUTO_DOC_SIM != "1":
        return
    try:
        from src.extraction.relation_builder import build_document_similarity_edges
        file_id = ctx.get("file_id")
        count = build_document_similarity_edges(file_id=file_id)
        logger.info(f"文件 {file_id} 文档相似度边构建完成: {count} 条")
    except Exception as e:
        logger.warning(f"文档相似度边构建失败（已忽略）: {e}")


@engine.register_stage("images", stage_type="async")
def _stage_images(ctx: dict):
    """图片提取：从原始文件提取内嵌图片，支撑可读模式 ![[图]] 显示

    查看层增强，不影响检索。失败降级（跳过），不阻断。
    """
    if RAG_IMAGE_EXTRACT != "1":
        return
    try:
        from config import IMAGES_DIR
        from .image_extractor import extract_images
        from src.storage import db
        file_id = ctx.get("file_id")
        target = ctx.get("target_path") or ctx.get("filepath")
        if not file_id or not target:
            return
        # 幂等：已有图片就不再重复提取
        if db.count_images(file_id) > 0:
            logger.info(f"文件 {file_id} 已有图片记录，跳过提取")
            return
        imgs = extract_images(str(target), file_id, IMAGES_DIR)
        if imgs:
            n = db.add_images(file_id, imgs)
            logger.info(f"文件 {file_id} 图片提取完成: {n} 张")
    except Exception as e:
        logger.warning(f"图片提取失败（已忽略）: {e}")
