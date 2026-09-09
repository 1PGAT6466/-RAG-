"""
Markdown 可读模式渲染
======================
将清洗后的 chunk 拼接为 Obsidian 式可读 Markdown（而非纯文本），
把知识库文档「读起来像 .md 渲染」而非「看原始文本块」。

设计原则：
- 不改变原始检索质量（本模块仅作用于「查看」层，不动入库 chunk）
- 保留 chunk 顺序，用分隔标题组织
- 识别常见板块（正文/表格/列表/代码），做轻量结构化
"""
import re
import logging

logger = logging.getLogger("rag.markdown_render")


def _clean_noise(text: str) -> str:
    """清理入库遗留的解析噪声（图片公式、纯 URL、空白等），但不破坏内容"""
    if not text:
        return ""
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        # 图片公式公式（WPS 粘贴图片）
        if s.startswith("=DISPIMG("):
            continue
        # 纯 URL 行
        if re.match(r"^https?://\S+$", s):
            continue
        # 纯空白分隔符
        if not s or set(s) <= {" ", "\t"}:
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def _classify_chunk(content: str) -> str:
    """粗略判断 chunk 类型，用于决定是否包代码块/引用块"""
    s = content.strip()
    if not s:
        return "text"
    # 表格：多行且含分隔符 | 或多空格对齐
    if s.count("|") > 3 or re.search(r"\n[ |+-]{5,}\n", s):
        return "table"
    # 代码/JSON：以 { 或 ``` 开头，或含大量花括号
    if s.startswith(("{", "```")) or (s.count("{") + s.count("}") > 10):
        return "code"
    # 列表：多行以 - / 数字点 开头
    if re.match(r"^[\s]*[-*•] |^[\s]*\d+\. ", s):
        return "list"
    return "text"


def chunks_to_markdown(file_name: str, chunks: list[dict]) -> str:
    """把文件的所有 chunk 拼成可读 Markdown

    chunks: [{id, chunk_index, content, ...}]
    返回带标题 + 分节的 Markdown 字符串
    """
    if not chunks:
        return f"# {file_name}\n\n（本文档暂无内容）\n"

    parts = [f"# {file_name}\n"]
    # 按 chunk_index 排序（保证文档顺序）
    ordered = sorted(chunks, key=lambda c: c.get("chunk_index", 0))

    for i, c in enumerate(ordered):
        content = _clean_noise(c.get("content", ""))
        if not content:
            continue
        kind = _classify_chunk(content)
        idx = c.get("chunk_index", i)
        # 章节分隔标题（Obsidian 目录感）
        parts.append(f"\n## 片段 {idx + 1}\n")
        if kind == "code":
            parts.append("```\n" + content + "\n```\n")
        elif kind == "table":
            # 表格原文可能非严格 Markdown，用引用块包裹避免破坏渲染
            parts.append("> 表格数据\n\n" + content + "\n")
        else:
            parts.append(content + "\n")

    md = "\n".join(parts)
    # 清理多余空行
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def render_chunk_readable(content: str) -> str:
    """单个 chunk 渲染为可读 Markdown（供详情页「可读模式」切换）"""
    content = _clean_noise(content)
    if not content:
        return ""
    kind = _classify_chunk(content)
    if kind == "code":
        return "```\n" + content + "\n```\n"
    if kind == "table":
        return content + "\n"
    return content + "\n"
