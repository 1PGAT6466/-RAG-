"""
图片提取器 — 从原始文档提取内嵌图片，支撑「Obsidian 式可读模式」的 ![[图]] 显示
==================================================================================

从已上传的原始文件（PDF/PPTX/PPT/XLSX/DOCX）提取图片，
存到 data/images/{file_id}/，并记录图片所属 page/slide 位置，
供可读模式按位置穿插渲染。

设计原则：
- 不改动检索/分块/向量主链路，图片是「查看层增强」
- 独立模块，失败降级空列表（不阻断入库）
- 图片按「页码」记录，可读时按顺序穿插；不做段落级精确锚定（避免重分块）
"""
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger("rag.image_extractor")

# 支持的图片扩展名
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}

# 单文件最多保留的图片数（控制磁盘/渲染负担，PPT 等元素图多的类型会触顶截断）
MAX_IMAGES_PER_FILE = 500


def extract_images(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """从原始文件提取图片，返回 [{page, filename, path, width, height}]

    按扩展名分发到对应提取器，失败/不支持返回空列表。
    """
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".pdf":
            result = _extract_pdf(filepath, file_id, images_dir)
        elif ext == ".pptx":
            result = _extract_pptx(filepath, file_id, images_dir)
        elif ext == ".ppt":
            # 旧版 OLE .ppt：python-pptx 不支持，尝试 LibreOffice 转 pdf 再提
            result = _extract_ppt_legacy(filepath, file_id, images_dir)
        elif ext in (".xlsx", ".xls"):
            result = _extract_xlsx(filepath, file_id, images_dir)
        elif ext == ".docx":
            result = _extract_docx(filepath, file_id, images_dir)
        else:
            logger.info(f"[图片] 不支持的类型 {ext}，跳过图片提取")
            return []
        # 全局上限截断（PPT 等元素图多的类型会触顶，控制磁盘负担）
        if len(result) > MAX_IMAGES_PER_FILE:
            logger.warning(f"[图片] {Path(filepath).name} 图片 {len(result)} 张超上限，截断到 {MAX_IMAGES_PER_FILE}")
            # 按图片面积降序取前 N（优先保留信息量大的图）
            result.sort(key=lambda x: -(x.get("width", 0) * x.get("height", 0)))
            result = result[:MAX_IMAGES_PER_FILE]
        return result
    except Exception as e:
        logger.warning(f"[图片] 提取失败 {filepath}: {e}")
        return []


def _save_image(image_id: int, file_id: int, images_dir: Path, data: bytes, ext: str) -> str | None:
    """保存一张图片，返回相对路径（如 images/{file_id}/{image_id}.png）"""
    subdir = images_dir / str(file_id)
    subdir.mkdir(parents=True, exist_ok=True)
    ext = ext.lstrip(".").lower() or "png"
    fname = f"{image_id}.{ext}"
    fpath = subdir / fname
    with open(fpath, "wb") as f:
        f.write(data)
    return f"images/{file_id}/{fname}"


def _extract_pdf(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """PDF 图片：优先提取页面上的内嵌图片对象；跳过整页扫描图（尺寸≈页面）

    扫描件 PDF 的每页是一张整页扫描大图，提取价值低且体积巨大（1423页→844MB），
    因此过滤「图片面积占页面面积 >60%」的整页扫描，只保留真正的插图/示意图/表格截图。
    """
    import fitz
    out = []
    idx = 0
    with fitz.open(filepath) as doc:
        total = len(doc)
        for page_no, page in enumerate(doc):
            imgs = page.get_images(full=True)
            page_area = page.rect.width * page.rect.height
            for img in imgs:
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    # 整页扫描过滤：图片在页面上的显示 bbox 覆盖整页（背景/扫描页），跳过
                    try:
                        bbox = page.get_image_bbox(img)
                        if bbox:
                            cover_w = bbox.width / page.rect.width
                            cover_h = bbox.height / page.rect.height
                            if cover_w > 0.9 and cover_h > 0.9:
                                # 图片铺满整页 → 扫描页背景，跳过
                                pix = None
                                continue
                    except Exception:
                        pass
                    # 过小的装饰图（横线/水印碎片）也跳过
                    if pix.width * pix.height < 64 * 64:
                        pix = None
                        continue
                    # 转 RGB（去掉 alpha 或 CMYK）
                    if pix.colorspace and pix.colorspace.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    data = pix.tobytes("png")
                    rel = _save_image(idx, file_id, images_dir, data, "png")
                    out.append({
                        "page": page_no,
                        "filename": f"{idx}.png",
                        "path": rel,
                        "width": pix.width,
                        "height": pix.height,
                    })
                    idx += 1
                    pix = None
                except Exception as e:
                    logger.warning(f"[图片] PDF 第{page_no}页图片提取失败: {e}")
            # 控制规模：一页最多 20 张（PPT 转 PDF 的页可能有大量元素图）
            if len(out) >= MAX_IMAGES_PER_FILE:
                logger.warning(f"[图片] PDF 图片达 {MAX_IMAGES_PER_FILE} 张上限，提前停止")
                break
            if (page_no + 1) % 300 == 0:
                logger.info(f"  [图片] PDF 提取中 ...{page_no + 1}/{total} 页")
    logger.info(f"[图片] PDF 提取完成: {len(out)} 张")
    return out


def _extract_pptx(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """PPTX 图片：遍历 slide 的 shape 和图片"""
    from pptx import Presentation
    out = []
    idx = 0
    prs = Presentation(filepath)
    for slide_no, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            # 直接图片 shape
            if shape.shape_type == 13:  # PICTURE
                try:
                    img = shape.image
                    rel = _save_image(idx, file_id, images_dir, img.blob, img.ext or "png")
                    out.append({
                        "page": slide_no,
                        "filename": f"{idx}.{img.ext or 'png'}",
                        "path": rel,
                        "width": getattr(img, "width", 0) or 0,
                        "height": getattr(img, "height", 0) or 0,
                    })
                    idx += 1
                except Exception as e:
                    logger.warning(f"[图片] PPTX 第{slide_no}页图片提取失败: {e}")
            # 组/其他含图片的 shape（递归简单处理：检查 blipFill）
            if shape.element.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip'):
                try:
                    img = shape.image
                    rel = _save_image(idx, file_id, images_dir, img.blob, img.ext or "png")
                    out.append({
                        "page": slide_no,
                        "filename": f"{idx}.{img.ext or 'png'}",
                        "path": rel,
                        "width": 0, "height": 0,
                    })
                    idx += 1
                except Exception:
                    pass
    logger.info(f"[图片] PPTX 提取完成: {len(out)} 张")
    return out


def _extract_ppt_legacy(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """旧版 .ppt (OLE)：尝试 LibreOffice 转 pdf 再提图片（若 LibreOffice 不可用则降级空）"""
    import subprocess, tempfile, shutil
    lo_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice", "libreoffice",
    ]
    soffice = None
    for p in lo_paths:
        try:
            if os.path.exists(p) or subprocess.run(["where", p], capture_output=True).returncode == 0:
                soffice = p
                break
        except Exception:
            continue
    if not soffice:
        logger.info("[图片] 旧版 .ppt 需 LibreOffice 转换，未检测到 LibreOffice，跳过图片提取")
        return []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, filepath],
                capture_output=True, timeout=300,
            )
            import glob
            pdfs = glob.glob(os.path.join(tmpdir, "*.pdf"))
            if not pdfs:
                return []
            return _extract_pdf(pdfs[0], file_id, images_dir)
    except Exception as e:
        logger.warning(f"[图片] .ppt 转 pdf 提取失败: {e}")
        return []


def _extract_xlsx(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """XLSX 图片：从 xl/media/ 提取，粗略按工作表顺序归 page"""
    import zipfile
    out = []
    idx = 0
    with zipfile.ZipFile(filepath) as z:
        media_files = [n for n in z.namelist() if n.startswith("xl/media/")]
        # 按名称排序（图片在 sheet 中的顺序大致对应）
        media_files.sort()
        for n in media_files:
            ext = Path(n).suffix.lower()
            if ext not in IMG_EXT:
                continue
            data = z.read(n)
            rel = _save_image(idx, file_id, images_dir, data, ext)
            out.append({
                "page": 0,  # xlsx 图片无明确页码，先归 page 0
                "filename": f"{idx}{ext}",
                "path": rel,
                "width": 0, "height": 0,
            })
            idx += 1
            if idx > 500:
                break
    logger.info(f"[图片] XLSX 提取完成: {len(out)} 张")
    return out


def _extract_docx(filepath: str, file_id: int, images_dir: Path) -> list[dict]:
    """DOCX 图片：从 word/media/ 提取"""
    import zipfile
    out = []
    idx = 0
    with zipfile.ZipFile(filepath) as z:
        media_files = [n for n in z.namelist() if n.startswith("word/media/")]
        media_files.sort()
        for n in media_files:
            ext = Path(n).suffix.lower()
            if ext not in IMG_EXT:
                continue
            data = z.read(n)
            rel = _save_image(idx, file_id, images_dir, data, ext)
            out.append({
                "page": 0,
                "filename": f"{idx}{ext}",
                "path": rel,
                "width": 0, "height": 0,
            })
            idx += 1
            if idx > 500:
                break
    logger.info(f"[图片] DOCX 提取完成: {len(out)} 张")
    return out
