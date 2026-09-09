"""
classification.py — 工业题材分类词典（单一权威来源）

对齐「伏羲」统一原则：文档自动分类（files.category）与查询分类加权
（ranking._CATEGORY_KW）必须共用同一套分类词典，杜绝两套表各写各的、
同义不同名（如「测试报告」vs「品质管理」、「设计手册」vs「机械设计」）导致
检索加权悄悄失效。

分类粒度 = 文档的工业题材类型，外加「操作手册」通用办公类，共 9 类 + 未分类。
关键词按优先级排列（先匹配先返回），面向「文件名 + 正文前 N 字」的粗分类场景。
"""

# 权威题材分类词典：类别名 → 关键词列表（小写匹配）
CATEGORY_DICT = {
    # 外购件/选型目录优先：带供应商+型号+图片链接特征的选型表，不是真正的标准紧固件
    "外购件选型": ["供应商", "选型", "型号", "米思米", "怡合达", "昶拓", "misumi", "link", "图片"],
    "连接器": ["连接器", "connector", "fakra", "端子", "接插件", "板端", "线端", "弯式", "直式"],
    "材料选型": ["材料", "选材", "材质", "金属", "塑料", "lcp", "铜材", "镀层", "电镀"],
    "工艺规程": ["工艺", "工序", "装配", "检测", "产线", "sop", "注塑", "焊锡", "组装"],
    "机械设计": ["齿轮", "轴承", "蜗杆", "蜗轮", "花键", "联轴器", "公差", "配合", "凸轮", "设计手册", "设计规范", "设计标准", "机械设计", "非标", "传动", "轴系", "结构设计"],
    "标准件": ["标准件", "标准", "规格", "gb/t", "iso", "din"],
    "品质管理": ["三坐标", "grr", "cpk", "spc", "位置度", "圆度", "检具", "测试", "试验", "报告", "结果"],
    "电气自动化": ["plc", "伺服", "变频器", "传感器", "接线", "hmi", "阻抗", "高频", "屏蔽"],
}

# 操作手册 / 通用办公文档分类（优先于工业题材词典判断）
# —— 办公系统（OA/EHR/IM）的操作/维护/使用手册不应被工业关键词误伤
MANUAL_CATEGORY = "操作手册"

# 操作手册判定关键词（文件名 + 正文前若干字）
MANUAL_KEYWORDS = [
    "使用手册", "操作手册", "操作指南", "使用指南", "用户手册", "用户指南",
    "维护手册", "后台维护", "管理员手册", "管理手册",
]

# 会被「操作手册」误伤的排除词：这些是工业/技术手册，不是办公操作手册
# 命中这些词的文档即使是「XX手册」，也回退到工业题材词典判断
MANUAL_EXCLUDE_KEYWORDS = [
    "设计手册", "技术手册", "工艺手册", "设计规范", "选型手册", "标准手册",
    "设备手册", "安装手册", "维修手册", "工程手册", "机械设计",
]

# 办公/OA 系统关键词（这些系统文档即使不带「手册」二字，也归「操作手册」）
OA_KEYWORDS = [
    "泛微", "e-cology", "ecology", "协同办公", "协同管理平台",
    "钉钉", "企业微信", "企微", "飞书", "feishu", "lark", "oa系统", "oa平台",
    "后端使用", "前端使用", "后台运维", "系统管理员", "后台设置",
]

# 系统名 → 虚拟文件夹（操作手册按发行系统分文件夹，实现「每个系统的操作手册一类」）
SYSTEM_FOLDER_MAP = [
    (["泛微", "e-cology", "ecology", "协同办公", "协同管理平台"], "泛微OA"),
    (["钉钉", "dingtalk"], "钉钉"),
    (["企业微信", "企微", "wecom", "wechatwork"], "企业微信"),
    (["飞书", "feishu", "lark"], "飞书"),
]

# 权威分类名列表（供校验 / 前端展示 / 文档输出）
CATEGORY_NAMES = list(CATEGORY_DICT.keys()) + [MANUAL_CATEGORY]

# 未匹配时的兜底分类
UNKNOWN_CATEGORY = "未分类"


def is_manual(text: str) -> bool:
    """判断是否为操作手册/办公系统文档（优先级最高，先于工业关键词）。

    带「设计手册/技术手册」等工业手册词的文档不算操作手册。
    """
    t = (text or "").lower()
    # 排除工业/技术手册
    if any(kw.lower() in t for kw in MANUAL_EXCLUDE_KEYWORDS):
        return False
    if any(kw.lower() in t for kw in MANUAL_KEYWORDS):
        return True
    if any(kw.lower() in t for kw in OA_KEYWORDS):
        return True
    return False


def detect_system_folder(text: str) -> str:
    """从文件名/正文识别发行系统，返回虚拟文件夹名（如「泛微OA」），识别不出返回空串。"""
    t = (text or "").lower()
    for keys, folder in SYSTEM_FOLDER_MAP:
        if any(k.lower() in t for k in keys):
            return folder
    return ""


def classify_text(text: str) -> str:
    """按关键词优先级返回首个命中的题材分类，未命中返回「未分类」。

    优先判断操作手册/办公系统文档（归「操作手册」），其次工业题材词典。
    text 通常为「文件名 + 空格 + 正文前若干字」拼接后的字符串。
    """
    if is_manual(text):
        return MANUAL_CATEGORY
    text_lower = (text or "").lower()
    for cat, keywords in CATEGORY_DICT.items():
        if any(kw.lower() in text_lower for kw in keywords):
            return cat
    return UNKNOWN_CATEGORY
