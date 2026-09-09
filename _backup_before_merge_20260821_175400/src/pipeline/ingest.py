"""
入库相关工具函数（分类等）

注：实际入库链路已统一由 engine.py（Stage 编排）+ ingest_stages.py（Stage 实现）驱动，
本模块仅保留被复用的纯工具函数（如 _auto_classify），不再维护独立 ingest() 管道。
"""
import logging

from src.classification import classify_text, detect_system_folder, is_manual

logger = logging.getLogger("rag.pipeline")


def _auto_classify(filename: str, text: str) -> str:
    """文档分类：文件名优先，正文兜底。

    先单独用文件名判断，能出结果就直接采用（文件名主旨信号最强，
    不被正文噪声稀释）；只有文件名无法判定（如「车辆.docx」这类裸名）时才
    拼正文前若干字再判。

    例外：文件名命中「标准件」但正文带供应商/型号/图片/链接等外购件选型
    特征时（如「标准件新表」实际是米思米/怡合达选型目录），归「外购件选型」。
    """
    name_lower = (filename or "").lower()
    name_only = classify_text(filename or "")
    # 「标准件」是易误导的宽泛词：文件名含标准件但正文是选型目录时，以正文为准
    if name_only == "标准件" and "标准件" in name_lower:
        body_cat = classify_text((text or "")[:2000])
        if body_cat == "外购件选型":
            return body_cat
    if name_only and name_only != "未分类":
        return name_only
    return classify_text((filename or "") + " " + (text or "")[:2000])


def _auto_folder(filename: str, text: str) -> str:
    """自动识别文档发行系统，返回虚拟文件夹路径（如 /泛微OA），识别不出返回空串。

    优先用文件名识别；裸文件名（如「车辆.docx」）才拼正文兜底。
    """
    folder = detect_system_folder(filename or "")
    if folder:
        return folder
    return detect_system_folder((filename or "") + " " + (text or "")[:2000])
