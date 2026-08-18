"""
入库相关工具函数（分类等）

注：实际入库链路已统一由 engine.py（Stage 编排）+ ingest_stages.py（Stage 实现）驱动，
本模块仅保留被复用的纯工具函数（如 _auto_classify），不再维护独立 ingest() 管道。
"""
import logging

from src.classification import classify_text

logger = logging.getLogger("rag.pipeline")


def _auto_classify(filename: str, text: str) -> str:
    """简单关键词分类（统一走 classification.CATEGORY_DICT 权威词典）"""
    return classify_text((filename or "") + " " + (text or "")[:2000])
