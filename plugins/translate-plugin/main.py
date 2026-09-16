"""
翻译工具插件

支持多语言互译，通过下拉框选择源语言和目标语言。
实际项目中可替换为调 LLM API 或翻译 API。
"""

import re

# 语言代码 → 中文名
_LANG_NAMES = {
    "auto": "自动检测",
    "zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文",
    "de": "德文", "fr": "法文", "es": "西班牙文", "ru": "俄文", "ar": "阿拉伯文",
}


def _detect_lang(text: str) -> str:
    """简单语言检测：含中文→zh，含日文假名→ja，含韩文→ko，否则→en"""
    if re.search(r'[\u4e00-\u9fff]', text):
        return "zh"
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', text):
        return "ja"
    if re.search(r'[\uac00-\ud7af]', text):
        return "ko"
    if re.search(r'[a-zA-Z]', text):
        return "en"
    return "unknown"


def translate(text: str = "", source_lang: str = "auto", target_lang: str = "en") -> dict:
    """将文本翻译为目标语言

    Args:
        text: 待翻译文本
        source_lang: 源语言代码（auto=自动检测, zh/en/ja/ko/de/fr/es/ru/ar）
        target_lang: 目标语言代码（zh/en/ja/ko/de/fr/es/ru/ar）
    """
    if not text.strip():
        return {"error": "文本为空"}

    # 自动检测源语言
    detected = _detect_lang(text)
    actual_source = detected if source_lang == "auto" else source_lang

    # 同语言无需翻译
    if actual_source == target_lang:
        return {
            "original": text,
            "translated": text,
            "source_lang": _LANG_NAMES.get(actual_source, actual_source),
            "target_lang": _LANG_NAMES.get(target_lang, target_lang),
            "note": "源语言与目标语言相同，无需翻译",
        }

    src_name = _LANG_NAMES.get(actual_source, actual_source)
    tgt_name = _LANG_NAMES.get(target_lang, target_lang)

    # 实际项目中这里应调 LLM API 或翻译 API
    # 示例返回：
    return {
        "original": text,
        "translated": f"[{src_name}→{tgt_name} 翻译示例: {text}]",
        "source_lang": src_name,
        "target_lang": tgt_name,
        "note": "示例插件，请替换为真实翻译 API",
    }


def detect_lang(text: str = "") -> dict:
    """检测文本语言"""
    if not text.strip():
        return {"error": "文本为空"}

    lang = _detect_lang(text)

    return {
        "text": text[:100],
        "language": lang,
        "language_name": _LANG_NAMES.get(lang, lang),
    }
