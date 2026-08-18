"""
入库相关工具函数（分类等）

注：实际入库链路已统一由 engine.py（Stage 编排）+ ingest_stages.py（Stage 实现）驱动，
本模块仅保留被复用的纯工具函数（如 _auto_classify），不再维护独立 ingest() 管道。
"""
import logging

logger = logging.getLogger("rag.pipeline")


def _auto_classify(filename: str, text: str) -> str:
    """简单关键词分类（规则按优先级，先匹配先返回）"""
    text_lower = (filename + " " + text[:2000]).lower()
    rules = [
        # 外购件/选型目录优先：带供应商+型号+图片链接特征的选型表，不是真正的标准紧固件
        ("外购件选型", ["供应商", "选型", "型号", "米思米", "怡合达", "昶拓", "misumi", "link", "图片"]),
        ("设计手册", ["设计手册", "设计规范", "设计标准", "机械设计"]),
        ("材料选型", ["材料", "选材", "材质", "金属", "塑料", "LCP"]),
        ("连接器", ["连接器", "connector", "fakra", "端子", "接插件"]),
        ("标准件", ["标准件", "标准", "规格", "GB/T", "ISO"]),
        ("工艺规程", ["工艺", "工序", "装配", "检测", "产线"]),
        ("测试报告", ["测试", "试验", "检测", "报告", "结果"]),
    ]
    for cat, keywords in rules:
        if any(kw.lower() in text_lower for kw in keywords):
            return cat
    return "未分类"
