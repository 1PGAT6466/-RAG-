"""
统一文档元素模型 — 对标 Unstructured/Docling 的 Element 体系。

所有 parser 返回 ParseResult（而非 str），下游 chunker/ingest 统一消费。
保留向后兼容：ParseResult.to_text() 等价于旧行为。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Element:
    """单个文档元素 — 文本/表格/图片/公式/标题。"""
    type: str                    # "title" | "heading" | "text" | "table" | "image" | "formula" | "page_header" | "page_footer"
    text: str                    # 文本内容（Markdown 格式）
    metadata: dict = field(default_factory=dict)
    # table 专用
    rows: Optional[list[list[str]]] = None   # 结构化行列数据
    table_html: str = ""                       # HTML 表格（保留合并单元格）
    # image 专用
    image_path: str = ""                       # 提取的图片文件路径
    image_caption: str = ""                    # 图片描述（OCR/多模态）

    @property
    def is_noise(self) -> bool:
        """是否为噪声元素（页眉页脚等）。"""
        return self.type in ("page_header", "page_footer")

    @property
    def is_structured(self) -> bool:
        """是否为结构化元素（表格/图片/公式）。"""
        return self.type in ("table", "image", "formula")

    def to_markdown(self) -> str:
        """转 Markdown 文本（表格保留结构）。"""
        if self.type == "table":
            if self.rows and len(self.rows) >= 1:
                lines = [" | ".join(c.replace("\n", " ") for c in self.rows[0])]
                if len(self.rows) >= 2:
                    lines.append(" | ".join(["---"] * len(self.rows[0])))
                    for row in self.rows[1:]:
                        lines.append(" | ".join(c.replace("\n", " ") for c in row))
                return "\n".join(lines)
            return self.text
        if self.type == "image":
            caption = self.image_caption or self.text or "图片"
            return f"![{caption}]({self.image_path})" if self.image_path else caption
        return self.text


@dataclass
class ParseResult:
    """文档解析结果 — 元素列表 + 元数据。"""
    elements: list[Element] = field(default_factory=list)
    source_file: str = ""
    total_pages: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def tables(self) -> list[Element]:
        return [e for e in self.elements if e.type == "table"]

    @property
    def images(self) -> list[Element]:
        return [e for e in self.elements if e.type == "image"]

    @property
    def text_elements(self) -> list[Element]:
        return [e for e in self.elements if e.type in ("text", "title", "heading")]

    @property
    def content_elements(self) -> list[Element]:
        """有效内容元素（排除噪声）。"""
        return [e for e in self.elements if not e.is_noise]

    def to_text(self) -> str:
        """向后兼容：转纯文本字符串（旧行为等价）。"""
        parts = []
        for e in self.elements:
            if e.is_noise:
                continue
            parts.append(e.to_markdown())
        return "\n\n".join(parts)

    def to_markdown(self) -> str:
        """输出完整 Markdown 文档。"""
        return self.to_text()

    def __len__(self) -> int:
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements)
