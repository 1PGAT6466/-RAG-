"""
文档解析器 — PDF / PPT / XLSX / DOCX / TXT
"""
import logging
from pathlib import Path
from config import RAG_PDF_OCR

logger = logging.getLogger("rag.parser")


def parse_file(filepath: str) -> str:
    """根据扩展名分发到对应解析器，返回纯文本"""
    ext = Path(filepath).suffix.lower()
    parsers = {
        ".pdf": _parse_pdf,
        ".ppt": _parse_ppt,
        ".pptx": _parse_pptx,
        ".xlsx": _parse_xlsx,
        ".xls": _parse_xlsx,
        ".docx": _parse_docx,
        ".txt": _parse_txt,
        ".md": _parse_txt,
    }
    parser = parsers.get(ext, _parse_txt)
    logger.info(f"解析 [{ext}]: {filepath}")
    return parser(filepath)


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
    if mode == "force":
        ocr_text = _parse_pdf_ocr(filepath)
        if ocr_text and len(ocr_text.strip()) > 100:
            return ocr_text
        logger.warning("强制 OCR 失败，回退文本层")
        return _extract_pdf_text(filepath)

    # 默认 auto：文本层提取 + 质量检测
    import fitz
    with fitz.open(filepath) as doc:
        n = len(doc)
        texts = []
        for i, page in enumerate(doc):
            t = page.get_text()
            if t:
                texts.append(t)
            if (i + 1) % 200 == 0:
                logger.info(f"  ...{i + 1}/{n} 页")
    text = "\n\n".join(texts)

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
    返回纯文本，失败返回空字符串。
    """
    import fitz

    try:
        from .ocr_engine import build_ocr, ocr_page_text
        ver, ocr = build_ocr(det_cap=960)
    except Exception as e:
        logger.warning(f"OCR 引擎初始化失败: {e}")
        return ""

    texts = []
    with fitz.open(filepath) as doc:
        total = len(doc)
        logger.info(f"OCR 解析开始: {total} 页")
        for i, page in enumerate(doc):
            try:
                pix = page.get_pixmap(dpi=150)
                import numpy as np
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                # RapidOCR 接受 RGB/BGR 数组或图片路径
                if img.shape[2] == 4:
                    img = img[:, :, :3]
                page_text = ocr_page_text(ocr, img)
                if page_text:
                    texts.append(page_text)
            except Exception as e:
                logger.warning(f"第 {i} 页 OCR 失败: {e}")
            if (i + 1) % 50 == 0:
                logger.info(f"  OCR ...{i + 1}/{total} 页")
    return "\n\n".join(texts)


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

    # 找 LibreOffice / soffice
    lo_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice",
        "libreoffice",
    ]
    soffice = None
    for p in lo_paths:
        if os.path.exists(p) or subprocess.run(["where", p], capture_output=True).returncode == 0:
            soffice = p
            break

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
                from .parser import _parse_pdf
                return _parse_pdf(pdfs[0])
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

    return f"[文件: {Path(filepath).name}] 旧版 .ppt 格式不支持直接解析，请用 PowerPoint 另存为 PDF 后重新上传。"


def _parse_xlsx(filepath: str) -> str:
    import openpyxl
    import re
    wb = openpyxl.load_workbook(filepath, data_only=True)
    texts = []

    # 噪声清洗：Excel 图片嵌入公式占位符、纯 URL、超长链接
    _img_formula = re.compile(r'^=DISPIMG\(', re.IGNORECASE)
    _url = re.compile(r'^https?://\S+$', re.IGNORECASE)

    def clean_cell(c):
        if c is None:
            return ""
        s = str(c).strip()
        # 图片占位公式（=DISPIMG(...)）整格格丢弃
        if _img_formula.match(s):
            return ""
        # 纯 URL 单元格丢弃（无信息量，且污染向量）
        if _url.match(s):
            return ""
        return s

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        texts.append(f"## {sheet_name}")
        for row in ws.iter_rows(values_only=True):
            # 清洗每个单元格，过滤噪声后再拼接
            cleaned = [clean_cell(c) for c in row]
            row_text = "\t".join(cleaned)
            # 去掉行内残留的空 tab（图片列/URL列清空后留下的空洞）
            row_text = re.sub(r'(\t)+', '\t', row_text).strip('\t').strip()
            if row_text:
                texts.append(row_text)
    return "\n".join(texts)


def _parse_docx(filepath: str) -> str:
    from docx import Document as DocxDocument
    doc = DocxDocument(filepath)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_txt(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


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
                    t = page.get_text()
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
