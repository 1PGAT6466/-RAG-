"""
docling 版面分析器 — PDF 结构化解析（对标 MinerU/RAGFlow 的版面分析层）。

用法：
    from src.pipeline.docling_parser import parse_pdf_with_docling
    result = parse_pdf_with_docling("doc.pdf")  # 返回 ParseResult

依赖：pip install docling（~500MB，含 PyTorch）
Feature Flag：RAG_DOCLING_PDF（config.py，"0" 禁用，"1" 启用）
降级链：docling 失败 → 自动降级到 fitz 主链路（零影响）
"""
import logging
from pathlib import Path

logger = logging.getLogger("rag.parser.docling")


def parse_pdf_with_docling(filepath: str) -> "ParseResult":
    """用 docling 对 PDF 做版面分析，返回结构化 ParseResult。

    识别类型：text / heading / table / image / page_header / page_footer / formula
    表格自动转为结构化 rows（保留行列）
    """
    from docling.document_converter import DocumentConverter
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from .elements import Element, ParseResult

    filepath = str(filepath)
    logger.info(f"docling 版面分析: {filepath}")

    try:
        # 配置：启用 OCR + 表格提取
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True

        converter = DocumentConverter(
            format_options={InputFormat.PDF: pipeline_options}
        )
        result = converter.convert(filepath)
        doc = result.document

        elements = []
        # 遍历 docling 输出的元素
        for item in doc.document_items:
            item_type = getattr(item, 'type', None)
            type_str = str(item_type) if item_type else 'text'

            # 获取文本内容
            text = ''
            if hasattr(item, 'text') and item.text:
                text = item.text.strip()
            elif hasattr(item, 'export_to_markdown'):
                try:
                    text = item.export_to_markdown().strip()
                except Exception:
                    pass

            if not text:
                continue

            # 映射 docling 类型到 Element 类型
            type_map = {
                'title': 'heading',
                'heading': 'heading',
                'section_header': 'heading',
                'table': 'table',
                'picture': 'image',
                'page_header': 'page_header',
                'page_footer': 'page_footer',
            }
            element_type = type_map.get(type_str, 'text')

            # 表格特殊处理：尝试获取结构化行列数据
            rows = None
            if element_type == 'table' and hasattr(item, 'export_to_dataframe'):
                try:
                    df = item.export_to_dataframe()
                    rows = [list(df.columns)]
                    for _, row in df.iterrows():
                        rows.append([str(c) for c in row])
                except Exception:
                    pass

            metadata = {}
            if hasattr(item, 'prov') and item.prov:
                prov = item.prov[0] if isinstance(item.prov, list) and item.prov else item.prov
                if hasattr(prov, 'page_no'):
                    metadata['page'] = prov.page_no

            elements.append(Element(
                type=element_type,
                text=text,
                rows=rows,
                metadata=metadata,
            ))

        if not elements:
            logger.warning(f"docling 未提取到元素: {filepath}")
            return ParseResult(source_file=filepath)

        logger.info(f"docling 完成: {len(elements)} 个元素 (tables={sum(1 for e in elements if e.type=='table')})")
        return ParseResult(elements=elements, source_file=filepath)

    except Exception as e:
        logger.error(f"docling 解析失败: {e}")
        raise  # 让调用方决定是否降级
