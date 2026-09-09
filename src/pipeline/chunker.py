"""
分块器 — 按段落/标题切分，保持语义完整性
"""
import re
import logging

logger = logging.getLogger("rag.chunker")

# 标题模式
HEADING_PAT = re.compile(r'^(#{1,6}\s+.+|[一二三四五六七八九十]+[、，.]\s*.+|第[一二三四五六七八九十百千]+[章节条款].+)', re.MULTILINE)

CHUNK_SIZE = 400   # 目标字符数（Chroma 2024 报告：200-token/无重叠最优，400 字符≈200 token）
CHUNK_OVERLAP = 0  # 重叠不提升检索精度，反而降低 IoU（Chroma 2024 实证）

# Contextual Retrieval 前缀（Anthropic 方案）：为每个 chunk 添加文档标题+章节上下文
# 仅影响 chunk 元数据中的 content，不影响原始文本
_CONTEXT_PREFIX_LEN = 80  # 前缀最大字符数


def chunk_text(text: str, source_name: str = "") -> list[dict]:
    """
    按标题 + 段落切分，返回 [{content, index, heading, source, markdown}, ...]
    """
    # 是否 Markdown 源（.md 文件）：保留原始 markdown 语义，可读模式无损渲染
    is_md = bool(source_name) and source_name.lower().endswith(".md")

    # 1. 按标题拆分为段落
    sections = _split_by_heading(text)

    chunks = []
    idx = 0
    for heading, body in sections:
        paras = _split_paragraphs(body)
        current = ""
        for para in paras:
            if not para.strip():
                continue
            # 如果当前块 + 新段落 > CHUNK_SIZE，保存当前块
            if current and len(current) + len(para) > CHUNK_SIZE:
                chunks.append({
                    "content": current.strip(),
                    "index": idx,
                    "heading": heading,
                    "source": source_name,
                    "markdown": is_md,
                })
                idx += 1
                # overlap: 保留末尾部分
                current = current[-CHUNK_OVERLAP:] + "\n" + para if len(current) > CHUNK_OVERLAP else para
            elif len(para) > CHUNK_SIZE:
                # 单段落超长：强制拆分，避免超出嵌入模型上下文
                for i in range(0, len(para), CHUNK_SIZE):
                    segment = para[i:i + CHUNK_SIZE]
                    if segment.strip():
                        chunks.append({
                            "content": segment.strip(),
                            "index": idx,
                            "heading": heading,
                            "source": source_name,
                            "markdown": is_md,
                        })
                        idx += 1
                current = ""
            else:
                current = (current + "\n" + para) if current else para
        # 最后一块
        if current.strip():
            chunks.append({
                "content": current.strip(),
                "index": idx,
                "heading": heading,
                "source": source_name,
                "markdown": is_md,
            })
            idx += 1

    logger.info(f"分块完成: {len(chunks)} 个 chunk")

    # Contextual Retrieval 前缀：为每个 chunk 添加文档名+章节上下文
    # 提升 BM25 精确匹配和嵌入语义质量（Anthropic 2024：减少 35-67% 检索失败）
    from config import RAG_CONTEXT_PREFIX
    if RAG_CONTEXT_PREFIX == "1" and source_name:
        for c in chunks:
            prefix_parts = [source_name]
            if c.get("heading"):
                prefix_parts.append(c["heading"])
            prefix = " | ".join(prefix_parts)
            if len(prefix) > _CONTEXT_PREFIX_LEN:
                prefix = prefix[:_CONTEXT_PREFIX_LEN]
            c["content"] = f"[{prefix}] {c['content']}"

    return chunks


def clean_chunks(chunks: list[dict]) -> list[dict]:
    """清洗层（独立于切块）：语言归一化 + 非中文过滤。

    对标 unstructured 的 `cleaners` 独立范式：切块（chunk_text）只做结构切分，
    语言清洗（繁转简 / 剔除日韩乱码 / 非中文过滤）由本函数独立承担，
    使清洗可单独测试、单独替换（如以后换 OpenCC 或加专业术语清洗）。

    历史坑（非中文文档整体误杀）：CAD、BOM、日志、纯英文/数字内容会被
    语言过滤整体过滤成 0 chunk。此处回退保留原始 chunk，避免
    「0 chunk → embed KeyError」整篇入库失败。
    """
    from .language_filter import normalize_chunks
    _raw_chunks = chunks  # 保留归一化前的原始 chunk（用于非中文文档回退）
    cleaned = normalize_chunks(chunks)
    if len(cleaned) == 0:
        logger.warning(
            "语言归一化后无有效 chunk（全部为非中文内容），回退保留原始 chunk，"
            f"共 {len(_raw_chunks)} 个。"
        )
        cleaned = _raw_chunks
    return cleaned


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """按标题分割成 [(标题, 正文), ...]"""
    parts = HEADING_PAT.split(text)
    sections = []
    if not parts:
        return [("", text)]

    # 如果开头不是标题，第一段无标题
    if not HEADING_PAT.match(parts[0]):
        sections.append(("", parts[0].strip()))
        parts = parts[1:]

    # 成对处理：标题 + 正文
    for i in range(0, len(parts) - 1, 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((heading, body))

    return sections if sections else [("", text)]


def _split_paragraphs(text: str) -> list[str]:
    """按空行拆分为段落"""
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
