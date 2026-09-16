"""
文档解析器 — PDF / PPT / XLSX / DOCX / TXT
"""
import logging
from pathlib import Path
from config import RAG_PDF_OCR
from .elements import Element, ParseResult

logger = logging.getLogger("rag.parser")

# 双栏判定最小 x 跨度（经验值，实测手册双栏跨宽 > 250pt，单栏 < 150pt）
_MULTI_COL_MIN_SPAN = 220.0


def parse_file(filepath: str) -> str:
    """根据扩展名分发到对应 backend，返回纯文本。

    分发逻辑抽到 src/pipeline/backends.py（对标 MinerU/docling 的 backend 抽象），
    此处只做「取 backend → 调 parse」。未知扩展名统一走 TextBackend（含二进制嗅探）。
    """
    p = Path(filepath)
    ext = p.suffix.lower()
    from .backends import get_backend
    backend = get_backend(ext)
    logger.info(f"解析 [{ext or '无扩展名'}]: {filepath} (backend={backend.__name__})")
    return backend().parse(filepath)


def _parse_pdf_elements(filepath: str) -> ParseResult:
    """PDF 结构化解析 — 返回 ParseResult（逐页元素列表）。

    不修改 _parse_pdf 本身，而是调用它获取完整文本后，
    用 fitz 逐页遍历并解析为结构化 Element 列表。
    """
    import fitz

    # 调用现有 _parse_pdf 获取完整文本（含 OCR 降级逻辑）
    full_text = _parse_pdf(filepath)

    # 用 fitz 逐页拆分，每页文本解析为 Element 列表
    elements: list[Element] = []
    with fitz.open(filepath) as doc:
        total_pages = len(doc)
        for i, page in enumerate(doc):
            page_text = page.get_text().strip()
            if not page_text:
                continue
            # 用 _page_text_reflowed 获取 markdown 化文本
            try:
                md_text = _page_text_reflowed(page)
            except Exception:
                md_text = page_text
            if not md_text or not md_text.strip():
                continue
            # 逐行解析 markdown 文本
            lines = md_text.split('\n')
            table_buf: list[list[str]] = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    # 空行：flush 表格缓冲
                    if table_buf:
                        md = '\n'.join([' | '.join(r) for r in table_buf])
                        elements.append(Element(
                            type='table', text=md, rows=table_buf,
                            metadata={'page': i, 'row_count': len(table_buf),
                                      'col_count': len(table_buf[0]) if table_buf else 0}
                        ))
                        table_buf = []
                    continue
                # 检测 markdown 标题
                if stripped.startswith('# '):
                    # flush 表格缓冲
                    if table_buf:
                        md = '\n'.join([' | '.join(r) for r in table_buf])
                        elements.append(Element(
                            type='table', text=md, rows=table_buf,
                            metadata={'page': i, 'row_count': len(table_buf),
                                      'col_count': len(table_buf[0]) if table_buf else 0}
                        ))
                        table_buf = []
                    elements.append(Element(type='heading', text=stripped[2:].strip(),
                                           metadata={'level': 1, 'page': i}))
                elif stripped.startswith('## '):
                    if table_buf:
                        md = '\n'.join([' | '.join(r) for r in table_buf])
                        elements.append(Element(
                            type='table', text=md, rows=table_buf,
                            metadata={'page': i, 'row_count': len(table_buf),
                                      'col_count': len(table_buf[0]) if table_buf else 0}
                        ))
                        table_buf = []
                    elements.append(Element(type='heading', text=stripped[3:].strip(),
                                           metadata={'level': 2, 'page': i}))
                elif stripped.startswith('### '):
                    if table_buf:
                        md = '\n'.join([' | '.join(r) for r in table_buf])
                        elements.append(Element(
                            type='table', text=md, rows=table_buf,
                            metadata={'page': i, 'row_count': len(table_buf),
                                      'col_count': len(table_buf[0]) if table_buf else 0}
                        ))
                        table_buf = []
                    elements.append(Element(type='heading', text=stripped[4:].strip(),
                                           metadata={'level': 3, 'page': i}))
                elif stripped.startswith('|') and '|' in stripped[1:]:
                    # 表格行：按 | 分割
                    cells = [c.strip() for c in stripped.split('|')]
                    # 去掉首尾空元素（split '|' 会在首尾产生空串）
                    if cells and cells[0] == '':
                        cells = cells[1:]
                    if cells and cells[-1] == '':
                        cells = cells[:-1]
                    # 跳过分隔行（如 |---|---|）
                    if all(set(c.strip()) <= {'-', ':'} for c in cells):
                        continue
                    table_buf.append(cells)
                else:
                    # flush 表格缓冲
                    if table_buf:
                        md = '\n'.join([' | '.join(r) for r in table_buf])
                        elements.append(Element(
                            type='table', text=md, rows=table_buf,
                            metadata={'page': i, 'row_count': len(table_buf),
                                      'col_count': len(table_buf[0]) if table_buf else 0}
                        ))
                        table_buf = []
                    elements.append(Element(type='text', text=stripped, metadata={'page': i}))
            # flush 残余表格缓冲
            if table_buf:
                md = '\n'.join([' | '.join(r) for r in table_buf])
                elements.append(Element(
                    type='table', text=md, rows=table_buf,
                    metadata={'page': i, 'row_count': len(table_buf),
                              'col_count': len(table_buf[0]) if table_buf else 0}
                ))

    # 如果 fitz 逐页解析未产出元素，用 full_text 兜底
    if not elements and full_text and full_text.strip():
        elements.append(Element(type='text', text=full_text.strip()))

    result = ParseResult(elements=elements, source_file=filepath, total_pages=total_pages)
    return result


def _parse_pdf(filepath: str) -> str:
    """PDF 解析 — 自动检测：文本层提取 → 乱码/扫描件检测 → 自动 GPU OCR

    中文 PDF 乱码根因：内嵌字体 ToUnicode CMap 映射不完整/损坏，
    提取出的汉字是「同形替换乱码」（每个字都是真汉字，但语义错了），
    字符级检测无法识别。可靠检测法：jieba 分词后多字词命中率
    （正常中文技术文档 ≥0.55，乱码 ≤0.45，随机汉字不成词）。

    Feature Flag：RAG_PDF_OCR
      - force：强制整本 OCR（跳过检测）
      - auto ：默认，文本层提取 + 自动检测，乱码/扫描件自动转 OCR
    """
    mode = RAG_PDF_OCR
    # 深度解析增强（可选）：开启 flag 时，优先用 MinerU 对复杂 PDF 做结构化抽取；
    #   MinerU 未安装/失败/产出过短时，自动降级回下面的 fitz+OCR 主链路（零影响）。
    try:
        from config import RAG_PDF_DEEP_PARSE
        if RAG_PDF_DEEP_PARSE == "1":
            from .deep_parse import deep_parse_pdf
            deep_text = deep_parse_pdf(filepath)
            if deep_text and len(deep_text.strip()) > 200:
                logger.info(f"MinerU 深度解析成功: {len(deep_text)} 字")
                return deep_text
            logger.info("MinerU 深度解析未产出有效文本，降级回 fitz+OCR")
    except Exception as e:
        logger.debug(f"深度解析前置检查跳过: {e}")

    # docling 版面分析（可选）：开启 flag 时，优先用 docling 对 PDF 做结构化版面分析；
    #   docling 未安装/失败/产出过短时，自动降级回下面的 fitz+OCR 主链路（零影响）。
    try:
        from config import RAG_DOCLING_PDF
        if RAG_DOCLING_PDF == "1":
            from src.pipeline.docling_parser import parse_pdf_with_docling
            result = parse_pdf_with_docling(filepath)
            text = result.to_text()
            if len(text.strip()) >= 50:
                logger.info(f"docling 版面分析成功 ({len(text)} 字)")
                return text
            else:
                logger.warning(f"docling 产出过短 ({len(text)} 字)，降级到 fitz")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"docling 版面分析失败: {e}，降级到 fitz")

    if mode == "force":
        ocr_text = _parse_pdf_ocr(filepath)
        if ocr_text and len(ocr_text.strip()) > 100:
            return ocr_text
        logger.warning("强制 OCR 失败，回退文本层")
        return _extract_pdf_text(filepath)

    # 默认 auto：文本层提取 + 质量检测
    import io
    import fitz
    with fitz.open(filepath) as doc:
        n = len(doc)
        buf = io.StringIO()
        for i, page in enumerate(doc):
            t = _page_text_reflowed(page)
            if t:
                if i > 0:
                    buf.write("\n\n")
                buf.write(t)
            if (i + 1) % 200 == 0:
                logger.info(f"  ...{i + 1}/{n} 页")
    text = buf.getvalue()

    # 检测 1：扫描件（文本层几乎为空）
    if n > 0 and len(text.strip()) < n * 30:
        logger.info(f"PDF 文本层过少（{len(text.strip())}字/{n}页），判定扫描件，自动转 OCR")
    # 检测 2：CMap 乱码（多字词命中率过低）
    elif _text_garbled_check(text):
        logger.info("PDF 文本层判定为 CMap 乱码，自动转 OCR")
    else:
        return text  # 正常，直接用文本层

    # 自动 OCR（GPU 加速）
    ocr_text = _parse_pdf_ocr(filepath)
    if ocr_text and len(ocr_text.strip()) > 100:
        return ocr_text
    logger.warning("OCR 失败，回退文本层（可能是乱码）")
    return text


def _text_garbled_check(text: str) -> bool:
    """检测中文文本是否为 CMap 同形替换乱码

    原理：随机汉字组合不成词，jieba 分词后多字词命中率骤降。
    全文均匀抽 24 段（每段 800 字），取最低 4 段的中位数 < 0.45 判乱码
    （低值簇判定：乱码 PDF 即使部分页正常，总有大量低值页）。
    实测：乱码手册最低簇 0.37-0.48；OCR 后正常文本最低簇 0.52+。
    """
    if len(text) < 400:
        return False  # 太短不判定
    import jieba
    nseg = 24
    span = max(1, len(text) // nseg)
    ratios = []
    for k in range(nseg):
        seg = text[k * span: k * span + 800]
        words = [w.strip() for w in jieba.lcut(seg)
                 if w.strip() and '\u4e00' <= w.strip()[0] <= '\u9fff']
        if len(words) < 20:
            continue  # 该段汉字太少（可能是目录/表格页），跳过
        multi = sum(1 for w in words if len(w) >= 2)
        ratios.append(multi / len(words))
    if len(ratios) < 4:
        return False
    ratios.sort()
    low4 = ratios[:4]
    low_median = (low4[1] + low4[2]) / 2  # 低 4 段的中位
    logger.info(f"乱码检测：抽样 {len(ratios)} 段，低簇中位={low_median:.3f}（阈值 0.45），"
                f"全文中位={ratios[len(ratios)//2]:.3f}")
    return low_median < 0.45


def _page_text_reflowed(page) -> str:
    """逐页提取文本，产出 Markdown 化结构（P1 改造）。

    使用 fitz dict API 提取字体信息，自动识别标题层级（大字体 = 标题）。
    双栏页保持重排逻辑不变。

    输出格式：
      # 大标题（字体 ≥ 正文 1.4x）
      ## 小标题（字体 ≥ 正文 1.2x）
      正常段落（正文）
      | 表格 |（如果检测到表格结构）
    """
    import fitz

    # 用 dict API 提取带字体信息的块
    try:
        d = page.get_text("dict")
        blocks = d.get("blocks", [])
    except Exception:
        return page.get_text()  # fallback

    if not blocks:
        return page.get_text()

    # 收集所有 span 的字体大小，确定正文基准字号
    font_sizes = []
    for b in blocks:
        if b.get("type") != 0:  # 只看文本块
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if text and len(text) > 1:
                    font_sizes.append(round(span.get("size", 12), 1))

    if not font_sizes:
        return page.get_text()

    # 正文基准字号：取众数（最常见的字号）
    from collections import Counter
    size_counter = Counter(font_sizes)
    body_size = size_counter.most_common(1)[0][0]

    # 构建 Markdown
    lines_out = []
    for b in blocks:
        if b.get("type") != 0:
            continue

        # 双栏判定：保持原有逻辑
        # （在 dict API 下，blocks 已按阅读序排列，双栏页需要重排）
        block_lines = []
        max_font = 0
        is_bold = False

        for line in b.get("lines", []):
            line_text_parts = []
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                line_text_parts.append(text)
                size = span.get("size", 12)
                if size > max_font:
                    max_font = size
                flags = span.get("flags", 0)
                if flags & 16:  # bit 4 = bold
                    is_bold = True

            line_text = " ".join(line_text_parts).strip()
            if line_text:
                block_lines.append(line_text)

        if not block_lines:
            continue

        block_text = " ".join(block_lines)

        # 标题检测：基于字体大小
        size_ratio = max_font / body_size if body_size > 0 else 1
        if size_ratio >= 1.4:
            lines_out.append(f"# {block_text}")
        elif size_ratio >= 1.2:
            lines_out.append(f"## {block_text}")
        elif is_bold and size_ratio >= 1.1 and len(block_text) < 100:
            # 加粗 + 稍大 + 短文本 → 三级标题
            lines_out.append(f"### {block_text}")
        else:
            lines_out.append(block_text)

    return "\n\n".join(lines_out)


def _extract_pdf_text(filepath: str) -> str:
    """用 PyMuPDF 提取 PDF 文本层（对 CMap 处理最好，速度快）"""
    import fitz
    texts = []
    with fitz.open(filepath) as doc:
        total = len(doc)
        for i, page in enumerate(doc):
            t = page.get_text()
            if t:
                texts.append(t)
            if (i + 1) % 200 == 0:
                logger.info(f"  ...{i + 1}/{total} 页")
    return "\n\n".join(texts)


def _parse_pdf_ocr(filepath: str) -> str:
    """PDF OCR：用 PyMuPDF 渲染页面 → RapidOCR，逐页识别

    大文件（几百上千页）耗时较长，但这是修复内嵌字体乱码的唯一可靠手段。
    返回纯文本（经后处理：页眉页脚/页号过滤 + 段落合并），失败返回空字符串。
    """
    import fitz

    try:
        from .ocr_engine import build_ocr, ocr_page_text
        ver, ocr = build_ocr(det_cap=960)
    except Exception as e:
        logger.warning(f"OCR 引擎初始化失败: {e}")
        return ""

    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy 未安装，OCR 解析无法执行")
        return ""

    page_texts = []
    with fitz.open(filepath) as doc:
        total = len(doc)
        logger.info(f"OCR 解析开始: {total} 页")
        for i, page in enumerate(doc):
            try:
                pix = page.get_pixmap(dpi=150)
                try:
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    if img.shape[2] == 4:
                        img = img[:, :, :3]
                    page_text = ocr_page_text(ocr, img)
                    if page_text:
                        page_texts.append((i, page_text))
                finally:
                    pix = None
            except Exception as e:
                logger.warning(f"第 {i} 页 OCR 失败: {e}")
            if (i + 1) % 50 == 0:
                logger.info(f"  OCR ...{i + 1}/{total} 页")

    # P2: OCR 后处理管线
    return _postprocess_ocr(page_texts)


def _postprocess_ocr(page_texts: list[tuple[int, str]]) -> str:
    """P2: OCR 后处理管线 — 页眉页脚/页号过滤 + 段落合并 + 重复行去重。

    输入：[(page_index, page_text), ...]
    输出：干净的纯文本
    """
    if not page_texts:
        return ""

    import re
    from collections import Counter

    # Step 1: 逐页拆行
    page_lines = []
    for idx, text in page_texts:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        page_lines.append((idx, lines))

    # Step 2: 页眉页脚检测 — 连续 ≥3 页出现的首行/末行视为页眉/页脚
    if len(page_lines) >= 3:
        first_lines = Counter()
        last_lines = Counter()
        for idx, lines in page_lines:
            if len(lines) >= 2:
                first_lines[lines[0]] += 1
                last_lines[lines[-1]] += 1

        # 出现频率 ≥ 30% 的首行/末行视为页眉/页脚
        threshold = max(3, len(page_lines) * 0.3)
        header_set = {line for line, count in first_lines.items() if count >= threshold}
        footer_set = {line for line, count in last_lines.items() if count >= threshold}
    else:
        header_set = set()
        footer_set = set()

    # Step 3: 页号过滤 — 独立数字行（1-4 位数，可能带 "-" 前缀/后缀）
    page_num_pat = re.compile(r'^[-\s]*\d{1,4}[-\s]*$')

    # Step 4: 逐页清理 + 收集
    cleaned_pages = []
    for idx, lines in page_lines:
        cleaned = []
        for i, line in enumerate(lines):
            # 跳过页眉（首行）
            if i == 0 and line in header_set:
                continue
            # 跳过页脚（末行）
            if i == len(lines) - 1 and line in footer_set:
                continue
            # 跳过页号
            if page_num_pat.match(line):
                continue
            cleaned.append(line)
        if cleaned:
            cleaned_pages.append("\n".join(cleaned))

    # Step 5: 段落合并 — 连续非空行合并为段落（以空行或标题行为分隔）
    result_parts = []
    for page_text in cleaned_pages:
        # 简单段落合并：连续行拼接，遇到空行/标题行分段
        paragraphs = []
        current = []
        for line in page_text.split("\n"):
            if not line.strip():
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
            else:
                current.append(line)
        if current:
            paragraphs.append(" ".join(current))
        result_parts.append("\n\n".join(paragraphs))

    return "\n\n".join(result_parts)


def _parse_pptx(filepath: str) -> str:
    from pptx import Presentation as PPTXPresentation
    prs = PPTXPresentation(filepath)
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        texts.append(t)
    return "\n\n".join(texts)


def _parse_ppt(filepath: str) -> str:
    """
    旧版 .ppt (OLE 格式) — 尝试 LibreOffice 转换，无可选则返回提示
    python-pptx 不支持 OLE 格式，OLE 二进制提取中文准确率极低
    """
    logger.warning(f"旧版 .ppt 格式 (OLE): {filepath}")

    # 尝试 LibreOffice 命令行转换
    import subprocess
    import tempfile
    import os

    # S18: 使用 backends.find_libreoffice() 公共函数
    from .backends import find_libreoffice
    soffice = find_libreoffice()

    if soffice:
        out_dir = tempfile.mkdtemp()
        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, filepath],
                capture_output=True, timeout=120
            )
            # 转为 PDF 后解析
            import glob
            pdfs = list(glob.glob(os.path.join(out_dir, "*.pdf")))
            if pdfs:
                from . import parser as _parser
                return _parser._parse_pdf(pdfs[0])
        except Exception as e:
            logger.error(f"LibreOffice 转换失败: {e}")
        finally:
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)

    # OLE 二进制提取作为降级尝试
    try:
        import struct
        with open(filepath, "rb") as f:
            data = f.read()

        texts = []
        i = 0
        while i < len(data) - 3:
            byte_pair = struct.unpack_from("<H", data, i)[0]
            if 0x4E00 <= byte_pair <= 0x9FFF:
                buf = []
                j = i
                while j < len(data) - 1:
                    bp = struct.unpack_from("<H", data, j)[0]
                    if 0x2000 <= bp <= 0xFFFF and bp not in (0xFFFE, 0xFFFF):
                        buf.append(chr(bp))
                        j += 2
                    else:
                        break
                raw = "".join(buf)
                if len(raw) >= 4:
                    texts.append(raw)
                i = j
            else:
                i += 2

        combined = "\n".join(texts)
        # 用关键词过滤（仅保留含技术词汇的行，这是清理 OLE 乱码最有效的方式）
        import re
        tech_kw = ["设计", "材料", "连接器", "标准", "工艺", "检测", "测试",
                   "尺寸", "公差", "要求", "功能", "目的", "分类",
                   "规格", "参数", "性能", "温度", "电压", "电流", "压力",
                   "安装", "装配", "生产", "制造", "质量", "控制",
                   "端子", "塑胶", "铜材", "电镀", "焊锡", "导通", "高频",
                   "电磁", "屏蔽", "阻抗", "应力", "硬度", "耐磨", "密封",
                   "平面度", "真直度", "无铅", "锡温", "耐高温", "变形",
                   "凸台", "结构", "表面", "镀层", "接触", "绝缘", "导体"]
        filtered = []
        for line in combined.split("\n"):
            l = line.strip()
            if l and len(l) >= 4:
                if any('\uD800' <= c <= '\uDFFF' for c in l):
                    continue
                if any(kw in l for kw in tech_kw):
                    filtered.append(l)
        result = "\n".join(filtered)
        if len(result.strip()) >= 200:
            return result
    except Exception as e:
        logger.error(f"PPT OLE 提取失败: {e}")

    # 不返回占位提示串（历史坑：提示串 >50 字会当正文入库污染向量库）。
    # 改为抛异常，让 _stage_parse 显式标记该文件解析失败，而非伪装成成功。
    raise ValueError(f"旧版 .ppt 格式无法解析（缺 LibreOffice），请另存为 PDF 后重新上传: {Path(filepath).name}")


def _parse_xls(filepath: str) -> str:
    """旧版 .xls (OLE/BIFF) — openpyxl 不支持，用 xlrd 解析；无 xlrd 时用 LibreOffice 转 xlsx。

    历史坑：旧版 .xls/.xlsx 共用 openpyxl，导致 .xls 一上传就抛 InvalidFileException 直接失败。
    """
    # 首选 xlrd（旧版 .xls 专用，读值快）
    try:
        import xlrd
        wb = xlrd.open_workbook(filepath)
        texts = []
        for sheet in wb.sheets():
            texts.append(f"## {sheet.name}")
            for r in range(sheet.nrows):
                row_vals = []
                for c in range(sheet.ncols):
                    cell = sheet.cell_value(r, c)
                    # 数值转字符串，去掉浮点尾零
                    if isinstance(cell, float):
                        cell = (f"{cell:g}")
                    row_vals.append(str(cell).strip())
                row_text = "\t".join(row_vals).strip()
                if row_text:
                    texts.append(row_text)
        return "\n".join(texts)
    except ImportError:
        logger.warning("xlrd 未安装，xls 回退 LibreOffice 转换")
    except Exception as e:
        logger.warning(f"xlrd 解析 xls 失败，回退 LibreOffice: {e}")

    # 回退 LibreOffice 转 xlsx
    try:
        import subprocess, tempfile, os, glob
        # S18: 使用 backends.find_libreoffice() 公共函数
        from .backends import find_libreoffice
        soffice = find_libreoffice()
        if soffice:
            out_dir = tempfile.mkdtemp()
            try:
                subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", out_dir, filepath],
                               capture_output=True, timeout=120)
                xlsx = glob.glob(os.path.join(out_dir, "*.xlsx"))
                if xlsx:
                    return _parse_xlsx(xlsx[0])
            finally:
                import shutil
                shutil.rmtree(out_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"xls LibreOffice 转换失败: {e}")

    raise ValueError(f"旧版 .xls 无法解析（缺 xlrd 与 LibreOffice）: {Path(filepath).name}")


def _parse_xlsx_elements(filepath: str) -> ParseResult:
    """XLSX 结构化解析 — 返回 ParseResult（保留表格行列结构）。

    每个 sheet 生成一个 Element(type='table', rows=..., text=...)。
    表头行为第一行，数据行为后续行。保留原有清洗逻辑。
    """
    import openpyxl
    import re

    wb = openpyxl.load_workbook(filepath, data_only=True)
    elements: list[Element] = []

    # 噪声清洗：Excel 图片嵌入公式占位符、纯 URL、超长链接
    _img_formula = re.compile(r'^=DISPIMG\(', re.IGNORECASE)
    _url = re.compile(r'^https?://\S+$', re.IGNORECASE)

    def clean_cell(c):
        if c is None:
            return ""
        s = str(c).strip()
        if _img_formula.match(s):
            return ""
        if _url.match(s):
            return ""
        return s

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            cleaned = [clean_cell(c) for c in row]
            # 去掉行内残留的空 tab（图片列/URL列清空后留下的空洞）
            row_text = "\t".join(cleaned)
            row_text = re.sub(r'(\t)+', '\t', row_text).strip('\t').strip()
            if row_text:
                # 按 tab 拆回 cells
                cells = [c.strip() for c in row_text.split('\t')]
                rows.append(cells)

        # 过滤全空行
        rows = [r for r in rows if any(c for c in r)]
        if not rows:
            continue

        # 每个 sheet 生成一个 table 元素
        md = '\n'.join([' | '.join(r) for r in rows])
        elements.append(Element(
            type='table', text=md, rows=rows,
            metadata={'sheet': sheet_name,
                      'row_count': len(rows),
                      'col_count': len(rows[0]) if rows else 0}
        ))

    return ParseResult(elements=elements, source_file=filepath)


def _parse_xlsx(filepath: str) -> str:
    """向后兼容：返回纯文本。"""
    return _parse_xlsx_elements(filepath).to_text()


def _parse_docx_elements(filepath: str) -> ParseResult:
    """DOCX 结构化解析 — 返回 ParseResult（保留表格行列结构）。"""
    from docx import Document as DocxDocument
    from docx.table import Table as _Table
    from docx.text.paragraph import Paragraph as _Para
    doc = DocxDocument(filepath)
    elements = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith('}p'):
            p = _Para(child, doc)
            t = p.text.strip()
            if not t:
                continue
            style_name = (p.style.name or '').lower()
            if 'heading' in style_name:
                try:
                    level = int(style_name.replace('heading', '').strip())
                except ValueError:
                    level = 1
                elements.append(Element(type='heading', text=t, metadata={'level': level}))
            else:
                elements.append(Element(type='text', text=t))
        elif child.tag.endswith('}tbl'):
            tbl = _Table(child, doc)
            rows = []
            for row in tbl.rows:
                cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                rows.append(cells)
            if rows:
                md = '\n'.join([' | '.join(r) for r in rows])
                elements.append(Element(
                    type='table', text=md, rows=rows,
                    metadata={'row_count': len(rows), 'col_count': len(rows[0]) if rows else 0}
                ))
    if not elements:
        for p in doc.paragraphs:
            t = p.text.strip()
            if t:
                elements.append(Element(type='text', text=t))
    return ParseResult(elements=elements, source_file=filepath)


def _parse_docx(filepath: str) -> str:
    """向后兼容：返回纯文本。"""
    return _parse_docx_elements(filepath).to_text()


def _parse_txt(filepath: str) -> str:
    """带多编码探测的文本读取（utf-8 → gb18030 → utf-16）。

    历史坑：硬编码 utf-8 + errors=replace 会把 GBK/GB2312 中文替换成乱码且不可挽回。
    """
    raw = None
    with open(filepath, "rb") as f:
        raw = f.read()
    if not raw:
        return ""

    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    # 兑底：强解 utf-8，无法解码的替换（罕见场景）
    return raw.decode("utf-8", errors="replace")


def _parse_csv(filepath: str) -> str:
    """CSV 结构化解析：保留列头 + 每行“列名=值”语义，避免落为无结构纯文本。

    历史坑：CSV 走 _parse_txt 纯文本整读，行列结构丢失，大片表格被硬切成碎片。
    """
    import csv
    text = _parse_txt(filepath)
    # 用 csv 模块按行解析（保留引号内逗号），sniff 自动识别分隔符/引号
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',\t;|')
    except Exception:
        dialect = csv.excel

    reader = csv.reader(text.splitlines(), dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return text

    # 首行作为表头（若无表头语义则整行拼接）
    header = [h.strip() for h in rows[0]]
    lines = ["\t".join(header)]
    for r in rows[1:]:
        # 列名=值 拼接，保留字段语义；行内照旧可以用 tab
        cells = []
        for i, v in enumerate(r):
            col = header[i] if i < len(header) else f"col{i}"
            cells.append(f"{col}={v.strip()}" if col else v.strip())
        lines.append("\t".join(cells))
    return "\n".join(lines)


def parse_pdf_streaming(filepath: str, on_batch, flush_pages: int = 20):
    """PDF 流式解析 — 逐页识别，每 flush_pages 页回调一次 on_batch。

    用于流式入库：边识别边交付，不必等整本解析完。
    自动检测文本层可用性：
      - 有文本层且不乱码 → 用 fitz 逐页 get_text（快）
      - 无文本层/乱码 → 逐页 OCR

    on_batch 签名: on_batch(batch_text: str, done_pages: int, total_pages: int)
    返回：全程识别的总文本（用于后续整本处理，如摘要输入），失败返回 ""。
    """
    import os
    import fitz
    import numpy as np

    mode = RAG_PDF_OCR

    with fitz.open(filepath) as doc:
        total = len(doc)

        # 预判：是否需要 OCR（无文本层 → 扫描件；乱码 → OCR）
        need_ocr = (mode == "force")
        if mode != "force":
            # 抽样判断文本层质量
            sample_text = ""
            for i in range(min(total, 10)):
                sample_text += doc[i].get_text()
            if len(sample_text.strip()) < 30 * min(total, 10):
                need_ocr = True
                logger.info(f"PDF 文本层过少，流式 OCR")
            elif _text_garbled_check(sample_text + "\n\n" + doc[total // 2].get_text()):
                need_ocr = True
                logger.info(f"PDF 文本层乱码，流式 OCR")

        ocr = None
        if need_ocr:
            try:
                from .ocr_engine import build_ocr, ocr_page_text
                _, ocr = build_ocr(det_cap=960)
            except Exception as e:
                logger.warning(f"OCR 初始化失败，回退文本层: {e}")
                ocr = None
                need_ocr = False

        all_texts = []
        buf = []
        for i, page in enumerate(doc):
            try:
                if need_ocr and ocr is not None:
                    pix = page.get_pixmap(dpi=150)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                        pix.height, pix.width, pix.n)
                    if img.shape[2] == 4:
                        img = img[:, :, :3]
                    from .ocr_engine import ocr_page_text
                    t = ocr_page_text(ocr, img)
                else:
                    t = _page_text_reflowed(page)
                if t and t.strip():
                    buf.append(t.strip())
                    all_texts.append(t.strip())
            except Exception as e:
                logger.warning(f"第 {i} 页解析失败: {e}")

            # 每 flush_pages 页交付一批
            if (i + 1) % flush_pages == 0 or (i + 1) == total:
                if buf:
                    batch = "\n\n".join(buf)
                    buf = []
                    try:
                        on_batch(batch, i + 1, total)
                    except Exception as e:
                        logger.warning(f"on_batch 回调失败: {e}")
        return "\n\n".join(all_texts)
