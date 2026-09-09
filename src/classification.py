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


def classify_document(filename: str, text: str) -> str:
    """文档分类：文件名优先，正文兜底。

    先单独用文件名判断，能出结果就直接采用（文件名主旨信号最强，
    不被正文噪声稀释）；只有文件名无法判定时才拼正文前若干字再判。

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


def detect_document_folder(filename: str, text: str) -> str:
    """自动识别文档发行系统，返回虚拟文件夹路径（如 /泛微OA），识别不出返回空串。

    优先用文件名识别；裸文件名（如「车辆.docx」）才拼正文兜底。
    """
    folder = detect_system_folder(filename or "")
    if folder:
        return folder
    return detect_system_folder((filename or "") + " " + (text or "")[:2000])


# ============================================================
# 元数据层：文档类型（doc_kind）+ 权威等级（authority）
# ============================================================
# 背景（专属化工业检索精准度）：
#   「FAKRA连接器规格」被采购流水（含几百条 FAKRA 采购记录）词频碾压了仅 4 chunk 的
#   产线工艺文档；「镀金层厚度要求」被 1585 chunk 的通用机械手册碾压 Foxconn 专项手册。
#   根因是检索链路只认「词频/语义相似」，不认「哪个文档才是某规格的权威来源」。
#
# 解法：给文件加 doc_kind（文档类型）+ authority（权威等级），在精确置顶层消费。
#   权威等级层级：工艺规程 > 技术手册/设计规范 > 选型目录 > 操作手册/品质报告 > 采购流水。
#   采购流水压到最低——它只记录「买过什么」，规格的权威定义在技术手册/工艺规程里。
#
# 纯规则判定（零 LLM，与 LLM 减负战略一致），可由管理员在文档管理界面覆盖（可选）。

# 文档类型 → 权威等级映射
DOC_KIND_AUTHORITY = {
    "工艺规程": 5,
    "技术手册": 4,
    "设计规范": 4,
    "选型目录": 3,
    "操作手册": 2,
    "品质报告": 2,
    "电气自动化": 3,
    "采购流水": 1,
    "未分类": 0,
}

# 采购流水判定的文件名关键词（选型类文件里，带这些词的实为采购/订单流水）
_PURCHASE_NAME_KW = ["采购", "订单", "供应商", "进货", "下单", "报价", "合同", "交付"]


def detect_doc_kind(filename: str, category: str, text: str = "") -> str:
    """判定文档类型（doc_kind），返回类型名。

    判定优先级（复用 classification 的成熟逻辑，不新造分类体系）：
      1. 工艺规程信号：文件名/正文含「工艺/工序/装配/检测/产线/SOP」→ 工艺规程（最高权威）
      2. 采购流水：选型类文件里，文件名含采购/订单/供应商等 → 采购流水
      3. 其余按 category → doc_kind 映射
      4. 兜底未分类
    """
    name_lower = (filename or "").lower()
    cat = category or "未分类"

    # 工艺规程信号优先：文件名含装配/检测/工艺等，即使分类落在「连接器」也是工艺文档
    _PROCESS_NAME_KW = ["工艺", "工序", "装配", "检测", "产线", "sop", "组装", "焊", "注塑"]
    if any(kw.lower() in name_lower for kw in _PROCESS_NAME_KW):
        return "工艺规程"

    # 采购流水特判：外购件选型类，但文件名明确是采购流水
    if cat == "外购件选型":
        if any(kw.lower() in name_lower for kw in _PURCHASE_NAME_KW):
            return "采购流水"

    # category → doc_kind 映射
    kind_map = {
        "工艺规程": "工艺规程",
        "连接器": "技术手册",
        "材料选型": "技术手册",
        "机械设计": "技术手册",
        "标准件": "设计规范",
        "外购件选型": "选型目录",
        "操作手册": "操作手册",
        "品质管理": "品质报告",
        "电气自动化": "电气自动化",
    }
    return kind_map.get(cat, "未分类")


def resolve_authority(doc_kind: str) -> int:
    """文档类型 → 权威等级。未识别类型归 0。"""
    return DOC_KIND_AUTHORITY.get(doc_kind, 0)


def detect_doc_meta(filename: str, category: str, text: str = "") -> tuple[str, int]:
    """一站式判定：返回 (doc_kind, authority)。供入库与回填统一调用。"""
    kind = detect_doc_kind(filename, category, text)
    return kind, resolve_authority(kind)
