"""
统一 OCR 引擎构建入口

优先 PP-OCRv6 small（新包 rapidocr，表格/公式页识别显著更好，σ/δ 等符号正确）
回退 PP-OCRv4 mobile（旧包 rapidocr_onnxruntime）

用法：
    from src.pipeline.ocr_engine import build_ocr, ocr_page_text
    ver, eng = build_ocr(det_cap=960)
    text = ocr_page_text(eng, img)
"""
import logging
import os
from config import RAG_OCR_DML

logger = logging.getLogger("rag.ocr")


def build_ocr(det_cap: int = 960):
    """构建 OCR 引擎。返回 (version, engine)。

    - DML 开关：RAG_OCR_DML（默认 1）
    - det_cap：det 输入最大边长（0 = 不限制）
    - 推理优化：跳过 cls（扫描件方向固定）、rec 批 16
    """
    use_dml = RAG_OCR_DML == "1"

    # ---- 优先 PP-OCRv6 small（新包）----
    try:
        from rapidocr import RapidOCR as OCRv6
        params = {
            "Global.use_cls": False,
            "Rec.rec_batch_num": 16,
            "EngineConfig.onnxruntime.use_dml": use_dml,
        }
        if not use_dml:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = 4
        if det_cap:
            params["Det.limit_type"] = "max"
            params["Det.limit_side_len"] = int(det_cap)
        eng = OCRv6(params=params)
        logger.info(f"OCR 引擎: PP-OCRv6 small（DML={use_dml}, det_cap={det_cap}）")
        return "v6", eng
    except Exception as e:
        logger.warning(f"PP-OCRv6 初始化失败，回退 v4: {e}")

    # ---- 回退 PP-OCRv4 mobile（旧包）----
    from rapidocr_onnxruntime import RapidOCR as OCRv4
    kw = dict(use_cls=False, rec_batch_num=16)
    if det_cap:
        kw.update(det_limit_type="max", det_limit_side_len=int(det_cap))
    if use_dml:
        kw.update(det_use_dml=True, rec_use_dml=True)
    else:
        kw.update(intra_op_num_threads=4, inter_op_num_threads=1)
    eng = OCRv4(**kw)
    logger.info(f"OCR 引擎: PP-OCRv4 mobile（DML={use_dml}, det_cap={det_cap}）")
    return "v4", eng


def ocr_page_text(engine, img) -> str:
    """对单页图片跑 OCR，返回纯文本。兼容 v6/v4 两种输出格式。"""
    try:
        res = engine(img)
    except TypeError:
        # v4 旧包签名: engine(img) → (result, elapsed)
        result, _ = engine(img)
        if result:
            return "\n".join(r[1] for r in result)
        return ""

    # v6 新包: RapidOCROutput（.txts 是文本列表；空结果为 None/空）
    if res is None:
        return ""
    txts = getattr(res, "txts", None)
    if txts:
        return "\n".join(str(t) for t in txts)
    return ""
