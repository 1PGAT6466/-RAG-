"""
分块器 — 按段落/标题切分，保持语义完整性
"""
import re
import logging

logger = logging.getLogger("rag.chunker")

# 标题模式
HEADING_PAT = re.compile(r'^(#{1,6}\s+.+|[一二三四五六七八九十]+[、，.]\s*.+|第[一二三四五六七八九十百千]+[章节条款].+)', re.MULTILINE)

CHUNK_SIZE = 800   # 目标字符数
CHUNK_OVERLAP = 100


def chunk_text(text: str, source_name: str = "") -> list[dict]:
    """
    按标题 + 段落切分，返回 [{content, index, heading, source}, ...]
    """
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
                })
                idx += 1
                # overlap: 保留末尾部分
                current = current[-CHUNK_OVERLAP:] + "\n" + para if len(current) > CHUNK_OVERLAP else para
            else:
                current = (current + "\n" + para) if current else para
        # 最后一块
        if current.strip():
            chunks.append({
                "content": current.strip(),
                "index": idx,
                "heading": heading,
                "source": source_name,
            })
            idx += 1

    logger.info(f"分块完成: {len(chunks)} 个 chunk")
    return chunks


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
