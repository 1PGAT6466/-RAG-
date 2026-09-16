"""
backends.py — 解析后端抽象层（对标 MinerU / docling 的 backend/ 分层）

设计原则（对齐伏羲「模块式替换而非缝补」）：
  - 这是「分发壳」：把「一个扩展名 → 一个解析函数」的平铺 dict，升级为
    「一个 backend → 一组格式」的能力分层。以后换某个解析引擎 = 换一个 backend，
    不动 parser.parse_file 入口、不动 _stage_parse。
  - 零迁移：本文件只是「壳」，实际解析仍复用 parser.py 里现成的 _parse_* 函数，
    避免一次性大迁移的风险。后续要真正替换某个后端（如换 PDF 引擎）时，
    再把对应 _parse_* 的实现挪进对应 backend。
  - 软依赖：只在模块内 import parser（同包），不引入任何新第三方依赖。

Backend 分类（守「纯净统一有序」，按能力分四类，不堆扩展名）：
  - CadBackend   工程/CAD 文件（.mi/.step/.stp/.dwg/...）——只做文件级索引
  - PdfBackend   PDF（.pdf）——文本层 → CMap 检测 → OCR → MinerU 四级链路
  - OfficeBackend Office 文档（.docx/.xlsx/.xls/.ppt/.pptx）——含 LibreOffice 兜底
  - TextBackend  纯文本（.txt/.md/.log/.csv 及未知扩展名）——多编码 + csv 语义
"""
import logging

logger = logging.getLogger("rag.parser.backend")


# S18: LibreOffice 路径发现 — 公共函数，消除 _parse_ppt / _parse_xls 重复代码
def find_libreoffice() -> str | None:
    """查找 LibreOffice soffice 可执行文件路径，返回路径或 None。"""
    import os
    import subprocess
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice",
        "libreoffice",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
        try:
            if subprocess.run(["where", p], capture_output=True).returncode == 0:
                return p
        except Exception:
            pass
    return None


class ParseBackend:
    """解析后端统一接口。子类实现 parse，失败抛 ValueError（由调用方显式标记失败）。"""

    def parse(self, filepath: str) -> str:
        raise NotImplementedError


class CadBackend(ParseBackend):
    """工程/CAD 文件：只索引文件名/图号，不解析二进制内容。

    为什么只做文件级索引：CAD/CAM 文件（.mi/.step/.stp/.dwg/...）是二进制/坐标/几何数据，
    没有可检索的文本语义。若按纯文本硬塞进知识库，会切出几万个无意义的坐标 chunk，
    向量化打爆 CPU/内存/API 额度，且污染检索结果。（用户 2026-09-04 确认：
    这些文件「也要进知识库，按文件名检索」，见 MEMORY）

    分发表 EXT_BACKEND 已保证只有 CAD 扩展名会路由到本 backend，
    故此处无需再校验扩展名，直接做文件级索引。
    """

    def parse(self, filepath: str) -> str:
        from pathlib import Path
        p = Path(filepath)
        ext = p.suffix.lower()
        stem = p.stem
        logger.info(f"工程文件 [{ext}] 仅文件级索引（不解析内容）: {stem}")
        return f"[工程文件] 文件名: {stem}"


class PdfBackend(ParseBackend):
    """PDF：复用 parser._parse_pdf（内部已含文本层→CMap检测→OCR→MinerU 四级链路）。"""

    def parse(self, filepath: str) -> str:
        from .parser import _parse_pdf
        return _parse_pdf(filepath)


class OfficeBackend(ParseBackend):
    """Office 文档（docx/xlsx/xls/ppt/pptx），按扩展名分发到 parser 的对应解析器。"""

    _DISPATCH = {
        ".docx": "_parse_docx",
        ".xlsx": "_parse_xlsx",
        ".xls": "_parse_xls",
        ".ppt": "_parse_ppt",
        ".pptx": "_parse_pptx",
    }

    def parse(self, filepath: str) -> str:
        from pathlib import Path
        from . import parser as _p
        ext = Path(filepath).suffix.lower()
        fn_name = self._DISPATCH.get(ext)
        if fn_name is None:
            raise ValueError(f"OfficeBackend 收到非 Office 扩展名: {ext}")
        return getattr(_p, fn_name)(filepath)


class TextBackend(ParseBackend):
    """纯文本（txt/md/log）+ CSV + JSON + XML + 未知扩展名（二进制 null 嗅探后兜底）。"""

    _CSV = (".csv",)
    _STRUCTURED = (".json", ".xml", ".svg", ".yaml", ".yml", ".toml", ".ini", ".cfg")

    def parse(self, filepath: str) -> str:
        from pathlib import Path
        from . import parser as _p
        ext = Path(filepath).suffix.lower()
        if ext in self._CSV:
            return _p._parse_csv(filepath)
        if ext in self._STRUCTURED:
            return _p._parse_txt(filepath)
        # 未知/文本扩展名：二进制嗅探（null 占比过高则拒绝），否则走多编码文本读取
        _sniff_reject_binary(filepath, ext)
        return _p._parse_txt(filepath)


class EpubBackend(ParseBackend):
    """EPUB 电子书解析（技术手册常见格式）。"""

    def parse(self, filepath: str) -> str:
        return _parse_epub(filepath)


class HtmlBackend(ParseBackend):
    """HTML 页面解析。"""

    def parse(self, filepath: str) -> str:
        return _parse_html(filepath)


class ImageBackend(ParseBackend):
    """图片 OCR 解析（PNG/JPG/BMP/TIFF）。"""

    def parse(self, filepath: str) -> str:
        return _parse_image_ocr(filepath)


def _sniff_reject_binary(filepath: str, ext: str):
    """未知/文本扩展名的二进制嗅探：含高比例 null 字节（典型二进制/压缩）则拒绝。

    历史坑：把二进制当纯文本读出一堆乱码污染向量库，故此处显式拒绝。
    该逻辑原内联在 parser.parse_file 的未知扩展名分支，抽到此独立函数供 TextBackend 复用。
    """
    from pathlib import Path
    with open(filepath, "rb") as f:
        head = f.read(8192)
    null_ratio = head.count(b"\x00") / max(1, len(head))
    if null_ratio > 0.05:
        raise ValueError(f"无法解析的二进制文件（{ext or '无扩展名'}）: {Path(filepath).name}")


# ============================================================
# 扩展名 → backend 分发表（单一事实源）
# ============================================================
# 未列出的扩展名统一走 TextBackend（含二进制嗅探兜底）。
EXT_BACKEND = {
    # CAD / 工程文件
    ".mi": CadBackend, ".step": CadBackend, ".stp": CadBackend, ".stl": CadBackend,
    ".bdl": CadBackend, ".pkg": CadBackend,
    ".dwg": CadBackend, ".dxf": CadBackend,
    ".igs": CadBackend, ".iges": CadBackend, ".prt": CadBackend, ".asm": CadBackend,
    # PDF
    ".pdf": PdfBackend,
    # Office
    ".docx": OfficeBackend, ".xlsx": OfficeBackend, ".xls": OfficeBackend,
    ".ppt": OfficeBackend, ".pptx": OfficeBackend,
    # EPUB
    ".epub": EpubBackend,
    # HTML
    ".html": HtmlBackend, ".htm": HtmlBackend,
    # 图片 OCR
    ".png": ImageBackend, ".jpg": ImageBackend, ".jpeg": ImageBackend,
    ".bmp": ImageBackend, ".tiff": ImageBackend, ".tif": ImageBackend,
}

# 默认后端（文本/未知扩展名）
DEFAULT_BACKEND = TextBackend


def get_backend(ext: str):
    """按扩展名返回 backend 类（无匹配则返回默认 TextBackend）。"""
    return EXT_BACKEND.get(ext, DEFAULT_BACKEND)


# === 新增格式解析函数 ===

def _parse_epub(filepath: str) -> str:
    """EPUB 电子书解析：提取所有章节文本。"""
    try:
        import ebooklib
        from ebooklib import epub
        from html.parser import HTMLParser
    except ImportError:
        raise ValueError("ebooklib 未安装，无法解析 EPUB")

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._text = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav"):
                self._skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self._text.append(t)
        def get_text(self):
            return "\n".join(self._text)

    book = epub.read_epub(filepath, options={"ignore_ncx": True})
    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html = item.get_content().decode("utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
        if text and len(text) > 10:
            parts.append(text)
    result = "\n\n".join(parts)
    if len(result.strip()) < 50:
        raise ValueError(f"EPUB 解析内容过少: {len(result)} 字")
    return result


def _parse_html(filepath: str) -> str:
    """HTML 页面解析：提取正文文本。"""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._text = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "head"):
                self._skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style", "head"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self._text.append(t)
        def get_text(self):
            return "\n".join(self._text)

    # 尝试多种编码
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                html = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise ValueError(f"HTML 文件无法解码: {filepath}")

    extractor = _TextExtractor()
    extractor.feed(html)
    result = extractor.get_text()
    if len(result.strip()) < 20:
        raise ValueError(f"HTML 解析内容过少: {len(result)} 字")
    return result


def _parse_image_ocr(filepath: str) -> str:
    """图片 OCR 解析：使用 RapidOCR 提取文字。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise ValueError("RapidOCR 未安装，无法解析图片")

    ocr = RapidOCR()
    result, _ = ocr(filepath)
    if not result:
        raise ValueError(f"图片 OCR 未识别到文字: {filepath}")
    # result 格式: [[box, text, score], ...]
    texts = [item[1] for item in result if item[2] > 0.5]
    text = "\n".join(texts)
    if len(text.strip()) < 10:
        raise ValueError(f"OCR 结果过少: {len(text)} 字")
    return text
