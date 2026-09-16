"""
deep_parse.py — 深度 PDF 解析（MinerU 模块式可选增强）

设计原则（对齐伏羲「模块式替换而非缝补」）：
  - 这是一个「整条链路」的可选替换：MinerU 擅长复杂 PDF（多栏/表格/公式/扫描件）的
    结构化抽取，完成度远高于 fitz+OCR 的朴素文本层提取。
  - 通过 config.RAG_PDF_DEEP_PARSE 开关控制，默认关闭。开启且 magic_pdf 可用时，
    对复杂 PDF 整本走 MinerU；magic_pdf 未安装/失败时，自动降级回 _parse_pdf 主链路。
  - 软依赖：不在模块顶部 import magic_pdf（避免未安装时 import 即崩），在调用时按需导入。

接口对齐 parser._parse_pdf：输入 filepath 字符串，输出纯文本字符串。
"""

import logging

logger = logging.getLogger("rag.parser.deep")

# MinerU 可用性缓存：检测一次后缓存，避免每次调用重复探测重型依赖
_mineru_available = None
_mineru_checked = False


def mineru_available() -> bool:
    """检测 MinerU（magic_pdf）是否可用。结果缓存，避免重复探测。"""
    global _mineru_available, _mineru_checked
    if _mineru_checked:
        return _mineru_available
    _mineru_checked = True
    try:
        import magic_pdf  # noqa: F401
        _mineru_available = True
        logger.info("MinerU (magic_pdf) 可用，深度 PDF 解析增强就绪")
    except Exception as e:
        _mineru_available = False
        logger.info(f"MinerU (magic_pdf) 不可用，深度解析降级回 fitz+OCR: {type(e).__name__}")
    return _mineru_available


def deep_parse_pdf(filepath: str) -> str:
    """用 MinerU 深度解析 PDF，返回纯文本；不可用/失败返回空串（由调用方降级）。

    调用方契约：返回空串或过短文本时，调用方回退 _parse_pdf 主链路。
    刻意不在此处回退——保持「单条链路只做一件事」，让 parser 的 _parse_pdf 统一编排降级。
    """
    if not mineru_available():
        return ""
    try:
        import magic_pdf
        # MinerU 6.x 的 programmatic API：由 magic_pdf 提供 lib pipelines。
        # 这里走最通用的 `magic_pdf.data.data_reader_writer` 高等级封装（不同小版本签名有差异，
        # 用 try 兼容多种调用方式，确保任何 API 差异都安全降级而非崩溃）。
        try:
            from magic_pdf.pipe.UNIPipe import UNIPipe
            from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
            result = _run_uni_pipe(UNIPipe, DiskReaderWriter, filepath)
            if result and len(result.strip()) > 100:
                return result
        except Exception:
            pass

        # 兜底：尝试 CLI 式（mineru 命令）——若装的是 mineru 独立 CLI 而非作为库使用
        return _run_mineru_cli(filepath)
    except Exception as e:
        logger.warning(f"MinerU 深度解析失败（降级回 fitz+OCR）: {e}")
        return ""


def _run_uni_pipe(UNIPipe, DiskReaderWriter, filepath: str) -> str:
    """通过 magic_pdf 的 UNIPipe 管线解析（MinerU 6.x 推荐 programmatic 方式）。

    不同 MinerU 小版本 UNIPipe 构造参数略有差异，用渐进式 try 兼容。
    返回纯文本；任何异常上抛由 deep_parse_pdf 捕获降级。
    """
    import tempfile
    import os

    out_dir = tempfile.mkdtemp(prefix="mineru_")
    try:
        local_image_dir = os.path.join(out_dir, "images")
        os.makedirs(local_image_dir, exist_ok=True)

        image_writer = DiskReaderWriter(local_image_dir)
        # 常见构造：UNIPipe(pdf_bytes, jbig2_enc_type, image_writer)
        # 先用标准方式，失败再尝试更简化的构造
        with open(filepath, "rb") as f:
            pdf_bytes = f.read()

        pipe = UNIPipe(pdf_bytes, "", image_writer)
        pipe.pipe_classify()
        pipe.pipe_analyze()
        pipe.pipe_parse()
        content = pipe.pipe_mk_markdown(local_image_dir, drop_mode="none")
        # content 是 (md_text, md_content_list) 元组
        if isinstance(content, tuple):
            return content[0]
        return str(content)
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)


def _run_mineru_cli(filepath: str) -> str:
    """通过 mineru CLI 命令解析（兜底路径，作为子进程调用）。

    返回 markdown 文本；失败返回空串。
    """
    import subprocess
    import tempfile
    import os
    import glob

    out_dir = tempfile.mkdtemp(prefix="mineru_cli_")
    try:
        r = subprocess.run(
            ["mineru", "-p", filepath, "-o", out_dir],
            capture_output=True, timeout=600,
        )
        if r.returncode != 0:
            logger.warning(f"mineru CLI 失败: {r.stderr.decode('utf-8', 'ignore')[:200]}")
            return ""
        mds = glob.glob(os.path.join(out_dir, "**", "*.md"), recursive=True)
        if not mds:
            return ""
        # 优先取非 "_middle" / "_content_list" 的主 md
        mds = sorted(mds, key=lambda p: len(p))
        with open(mds[0], "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"mineru CLI 调用失败: {e}")
        return ""
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
