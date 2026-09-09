"""
entity_extractor.py — 实体抽取（阶段 2）

两种抽取方式，可降级：
  1. 规则抽取（零 LLM，保底）：型号正则 + 材料词典 + 标准号正则 + 工艺参数
  2. LLM 抽取（MiMo/DeepSeek 增强）：非结构化 chunk → 结构化实体列表

返回统一格式：
  [
    {"name": "...", "type": "...", "aliases": [...], "description": "", "attributes": {}},
    ...
  ]

type 取值：connector(连接器/型号) | material(材料) | standard(标准) | process(工艺) | param(参数)
"""
import json
import logging
import re

logger = logging.getLogger("rag.extraction")

# === 领域规则（保底抽取）===

# 连接器型号词典：工业连接器型号有限可枚举，用白名单避免泛化正则误伤表格噪声
CONNECTOR_DICT = {
    "FAKRA": "FAKRA 系列连接器",
    "Mini-FAKRA": "小型化 FAKRA 连接器",
    "HSD": "高速数据连接器",
    "HSP": "高压连接器",
    "H-MTD": "H-MTD 连接器",
    "MTD": "MTD 连接器",
    "HS4D": "HS4D 连接器",
    "RJ45": "RJ45 网络连接器",
    "USB": "USB 连接器",
    "HDMI": "HDMI 连接器",
    "M8": "M8 圆形连接器",
    "M12": "M12 圆形连接器",
    "AK2": "AK2 系列连接器",
}

# 连接器型号正则：明确的品牌/系列前缀（如 MLG12 系列），避免泛化 `[A-Z]\d` 误伤
# 仅匹配已知系列前缀 + 数字后缀
CONNECTOR_PATTERNS = [
    r'\bMini-?FAKRA\b',
    r'\bMLG\d{1,3}(?:[-/]\d+)?\b',   # MLG 系列：MLG12, MLG13, MLG12-45
    r'\b(?:RJ45|USB|HDMI|M8|M12)\b',
]

# 材料词典（工业常见）
MATERIAL_DICT = {
    "LCP": "液晶聚合物",
    "PA66": "尼龙66",
    "PA6": "尼龙6",
    "PBT": "聚对苯二甲酸丁二醇酯",
    "PP": "聚丙烯",
    "ABS": "丙烯腈-丁二烯-苯乙烯",
    "PPS": "聚苯硫醚",
    "PEEK": "聚醚醚酮",
    "C7025": "铜合金",
    "C7035": "铜合金",
    "PA46": "尼龙46",
    "PA9T": "尼龙9T",
    "PA10T": "尼龙10T",
    "PA6T": "尼龙6T",
    "铜合金": "铜合金",
    "黄铜": "黄铜",
    "磷青铜": "磷青铜",
    "不锈钢": "不锈钢",
    "陶瓷": "陶瓷",
}

# 玻纤/增强标记（如 GF30、GF15），应过滤，不算独立型号
_GF_PATTERN = re.compile(r'\bGF\s?\d+\b', re.IGNORECASE)

# 工艺关键词
PROCESS_KEYWORDS = ["装配", "焊接", "冲压", "注塑", "电镀", "镀金", "镀锡", "压接", "铆接", "热处理"]

# 实体别名字典（同义归并）：抽到实体时自动补别名，提升检索同义匹配 + 图谱归并
# key: 实体名；value: 别名列表（全称/缩写/俗名/常见变体）
ENTITY_ALIAS_MAP = {
    # 材料：全称 ↔ 俗称 ↔ 常见牌号
    "铜合金": ["紫铜", "纯铜", "T2"],
    "黄铜": ["H62", "H65"],
    "不锈钢": ["SUS304", "SUS302", "SUS316", "不锈钢304", "1Cr18Ni9"],
    "磷青铜": ["QSn", "锡青铜"],
    "PEEK": ["聚醚醚酮"],
    "PPS": ["聚苯硫醚"],
    "LCP": ["液晶聚合物", "液晶高分子"],
    "PA66": ["尼龙66", "尼龙 66"],
    "PA6": ["尼龙6", "尼龙 6"],
    "PBT": ["聚对苯二甲酸丁二醇酯"],
    "ABS": ["丙烯腈-丁二烯-苯乙烯"],
    # 工艺：同义词
    "镀金": ["金镀层", "镀金层"],
    "电镀": ["电镀工艺", "镀覆"],
    "焊接": ["焊工艺", "锡焊"],
    "热处理": ["热工艺"],
    "注塑": ["注射成型", "注塑成型"],
}

# 材料-工艺相容性知识表（领域客观事实，用于建语义边 compatible_process）
MATERIAL_PROCESS_MAP = {
    "黄铜": ["热处理", "焊接", "电镀", "冲压", "压接"],
    "磷青铜": ["热处理", "电镀", "冲压", "压接"],
    "铜合金": ["热处理", "电镀", "冲压", "压接"],
    "不锈钢": ["热处理", "焊接", "电镀", "冲压"],
    "陶瓷": ["装配"],
    "LCP": ["注塑", "焊接"],
    "PA66": ["注塑", "焊接"],
    "PA6": ["注塑"],
    "PA6T": ["注塑", "焊接"],
    "PA46": ["注塑"],
    "PA9T": ["注塑"],
    "PA10T": ["注塑"],
    "PBT": ["注塑", "电镀"],
    "PPS": ["注塑", "焊接"],
    "PEEK": ["注塑"],
    "ABS": ["注塑", "电镀"],
}

# 标准分类知识表：根据标准号前缀判断其内容领域，用于建「标准→领域」语义
# 前缀匹配（前缀→领域类别），命中即归类，未命中归为「其他」
# 领域词表与 STANDARD_DOMAINS 严格一致（规则与 LLM 共用同一套词，避免同义不同名）
STANDARD_CATEGORY_RULES = [
    # 基础标准（GB/T 1.x 标准化导则）
    (r'^GB/T\s*\d\.', '基础标准'),
    # 材料（精确区间先匹配，避免被宽规则误吞）
    (r'^GB/T\s*12[2-9][0-9](?:\.\d+)?', '材料'),  # GB/T 1220 不锈钢/1229 钢/1298 工具钢
    (r'^GB/T\s*15[0-9]{2}(?:\.\d+)?', '材料'),     # GB/T 1591 低合金钢
    (r'^GB/T\s*3[0-9]{3}', '材料'),                # GB/T 3077 合金钢
    (r'^GB/T\s*2[0-9]{3}', '材料'),                # GB/T 2040/2050 有色金属
    (r'^GB/T\s*6[0-9]{2}(?:\.\d+)?', '材料'),     # GB/T 699/700 钢铁材料
    (r'^GB/T\s*7[0-9]{2}(?:\.\d+)?', '材料'),     # GB/T 702/706/709 型钢
    # 机械制图（4 位）
    (r'^GB/T\s*4[0-9]{3}', '机械制图'),            # GB/T 4458/4459 制图标准
    (r'^GB/T\s*1[0-9]{3}', '机械制图'),            # GB/T 13361/14689 技术制图
    # 工艺（5xxx，3 位以上）
    (r'^GB/T\s*5[0-9]{3}', '工艺'),                # GB/T 5185 焊接术语
    # 紧固件：标准号区间 800-999（8xx/9xx，机械标准件核心范围）
    (r'^GB/T\s*[89][0-9]{2}(?:\.\d+)?', '紧固件'),
    # 紧固件：标准号 10-99 两位数（GB/T 27/65/70/93/97 等螺钉/垫圈/销）
    (r'^GB/T\s*[1-9][0-9](?:\.\d+)?', '紧固件'),
    # 电工（IEC 前缀，内容必为电工电子领域）
    (r'^IEC\s*', '电工'),
]

# 参数（param）规则抽取：工业常见参数名 + 数值 + 单位
# 格式：(参数名, 匹配正则, 值捕获组, 单位捕获组)
PARAM_PATTERNS = [
    ("阻抗", r'阻抗\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(Ω|欧姆|ohm)?'),
    ("频率", r'(?:频率|工作频率)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)?'),
    ("额定电压", r'额定电压\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(V|kV|mV)?'),
    ("额定电流", r'额定电流\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(A|mA)?'),
    ("温度范围", r'(?:温度范围|耐温|工作温度)\s*[:：]?\s*([-—]?\d+(?:\.\d+)?)\s*(?:~|～|至|-)\s*([-—]?\d+(?:\.\d+)?)?\s*(℃|°C)?'),
    ("插拔寿命", r'(?:插拔寿命|插拔次数|寿命)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(次|万次)?'),
    ("接触电阻", r'接触电阻\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mΩ|Ω)?'),
    ("镀层厚度", r'(?:镀层厚度|镀金厚度|镀层)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(μm|um|微米|u\"|μ\")?'),
    ("绝缘电阻", r'绝缘电阻\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(MΩ|GΩ|Ω)?'),
    ("介电常数", r'介电常数\s*[:：]?\s*(\d+(?:\.\d+)?)'),
    ("耐压", r'(?:耐压|耐电压|介电强度)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(kV|V|kV/mm)?'),
    ("爬电距离", r'爬电距离\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm)?'),
]

# 标准号正则（跨换行匹配：PDF 中标准号常被换行拆开，如 "GB/T\n157-2001"）
# 模式：前缀 + 空格/换行 + 数字(可有小数点) + 可选年份
STANDARD_PATTERNS = [
    r'GB/T[\s\n]*\d{2,6}(?:\.\d+)?(?:[-—]\d{2,4})?',
    r'QC/T[\s\n]*\d{2,6}(?:\.\d+)?(?:[-—]\d{2,4})?',
    r'ISO[\s\n]*\d{2,6}(?:[-:]\d{2,4})?',
    r'IEC[\s\n]*\d{2,6}(?:[-:]\d{2,4})?',
    r'DIN[\s\n]*\d{2,6}(?:[-:]\d{2,4})?',
]

# 标准号主体：前缀 + 数字（不含年份，用于归并同号不同年份）
_STANDARD_BODY = re.compile(r'^([A-Z]{2,4}/?T?)\s*([\d.]+)')


def _normalize_standard_name(raw: str) -> str | None:
    """规范化标准号：
    1. 清洗换行/多余空格（GB/T\n157-2001 → GB/T 157-2001）
    2. 提取主体（去掉年份后缀），返回规范化主体如 "GB/T 157"
    返回 None 表示无效碎片（数字部分过短）
    """
    # 清洗换行和空白
    s = re.sub(r'[\s\n\r]+', ' ', raw).strip()
    # 去掉年份后缀（-1999 / -1976 / —2001 等）
    s_no_year = re.sub(r'[-—]\d{2,4}$', '', s).strip()
    m = _STANDARD_BODY.match(s_no_year)
    if not m:
        return s_no_year
    prefix = m.group(1)
    num = m.group(2).rstrip('.')
    # 数字部分至少 2 位，避免 GB/T 1、GB/T 8 这类 PDF 碎片
    digits = num.replace('.', '')
    if len(digits) < 2:
        return None
    return f"{prefix} {num}"


def _match_word(pattern: str, text: str) -> bool:
    """词匹配：纯 ASCII 词用 \b 边界（避免子串误命中），含中文词用子串直接匹配

    （\b 对中文边界无效，这是历史 bug 根因：中文材料词如「铜合金/不锈钢」用 \b 永远匹配不到）
    """
    if pattern.isascii():
        return re.search(r'\b' + re.escape(pattern) + r'\b', text, re.IGNORECASE) is not None
    return pattern in text


def extract_rule(text: str) -> list[dict]:
    """规则抽取：返回结构化实体列表（无需 LLM）"""
    if not text:
        return []
    entities = []

    # 先抽材料（优先，避免材料牌号被误识别为型号）
    seen_material = set()
    for mat, desc in MATERIAL_DICT.items():
        if _match_word(mat, text):
            key = mat.lower()
            if key in seen_material:
                continue
            seen_material.add(key)
            entities.append({
                "name": mat,
                "type": "material",
                "aliases": [mat],
                "description": desc,
                "attributes": {},
            })

    # 不锈钢牌号（SUS304/1Cr18Ni9 等）→ 归并到「不锈钢」material，牌号记入 aliases/variants
    alloy_variants = {m.group(0).replace(" ", "") for m in _STAINLESS_ALLOY.finditer(text)}
    if alloy_variants:
        # 找已识别的不锈钢实体（材料循环里可能已产出），否则新建
        ss_entity = next((e for e in entities if e.get("name") == "不锈钢" and e.get("type") == "material"), None)
        if ss_entity is None:
            ss_entity = {
                "name": "不锈钢",
                "type": "material",
                "aliases": [],
                "description": "不锈钢",
                "attributes": {},
            }
            entities.append(ss_entity)
            seen_material.add("不锈钢")
        # 合并牌号到 aliases + variants
        merged_aliases = set(ss_entity.get("aliases", []) or [])
        merged_aliases.update(alloy_variants)
        ss_entity["aliases"] = sorted(merged_aliases)
        variants = set((ss_entity.get("attributes") or {}).get("variants", []) or [])
        variants.update(alloy_variants)
        ss_entity["attributes"] = {**ss_entity.get("attributes", {}), "variants": sorted(variants)}

    # 连接器型号（词典优先，带描述；正则兜底补系列号）
    seen = set()
    material_names = {m.lower() for m in MATERIAL_DICT}
    # 1) 词典匹配（精确型号，带描述）
    for name, desc in CONNECTOR_DICT.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text, re.IGNORECASE):
            if name.upper() in seen:
                continue
            seen.add(name.upper())
            entities.append({
                "name": name,
                "type": "connector",
                "aliases": [name.upper()],
                "description": desc,
                "attributes": {},
            })
    # 2) 正则匹配（系列前缀 + 数字后缀，如 MLG12-45）
    for pat in CONNECTOR_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            name = m.group(0).strip()
            if name.upper() in seen:
                continue
            if name.lower() in material_names:
                continue  # 材料词不当型号
            if _GF_PATTERN.fullmatch(name):
                continue  # 玻纤标记不当型号
            seen.add(name.upper())
            entities.append({
                "name": name,
                "type": "connector",
                "aliases": [name.upper()],
                "description": "",
                "attributes": {},
            })

    # 标准号（规范化：清洗换行 + 归并年份 + 过滤碎片）
    seen_standard = set()
    for pat in STANDARD_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            norm = _normalize_standard_name(m.group(0))
            if not norm:
                continue  # 碎片，丢弃
            if norm in seen_standard:
                continue
            seen_standard.add(norm)
            entities.append({
                "name": norm,
                "type": "standard",
                "aliases": [],
                "description": "",
                "attributes": {},
            })

    # 工艺（关键词）
    for kw in PROCESS_KEYWORDS:
        if kw in text:
            entities.append({
                "name": kw,
                "type": "process",
                "aliases": [],
                "description": "",
                "attributes": {},
            })

    # 参数（param）：工业常见参数 + 数值 + 单位
    seen_param = set()
    for pname, pattern in PARAM_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        if pname in seen_param:
            continue
        seen_param.add(pname)
        attrs = {}
        groups = m.groups()
        if pname == "温度范围":
            # 范围型：value = "-40~105"，unit = ℃
            if groups[0]:
                lo = groups[0]
                hi = groups[1] if groups[1] else ""
                attrs["value"] = f"{lo}~{hi}" if hi else lo
            if len(groups) > 2 and groups[2]:
                attrs["unit"] = groups[2]
            elif groups[1] and groups[1] not in (None, ""):
                # 无单位捕获，兜底
                attrs["unit"] = "℃"
            else:
                attrs["unit"] = "℃"
        else:
            if groups and groups[0]:
                attrs["value"] = groups[0]
            # 单位：取第一个非空组
            for g in groups[1:]:
                if g:
                    attrs["unit"] = g
                    break
        entities.append({
            "name": pname,
            "type": "param",
            "aliases": [],
            "description": "",
            "attributes": attrs,
        })

    return _dedup(normalize_entities(entities))


def _dedup(entities: list[dict]) -> list[dict]:
    """按 (name, type) 去重"""
    seen = set()
    out = []
    for e in entities:
        key = (e["name"], e["type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# === 实体规范化（阶段 2 质量提升）===

# 系列前缀模式：MLG12-45 → 系列 MLG12 + 规格 45（连接器系列型号归并）
_SERIES_PATTERN = re.compile(r'\b([A-Z]{1,6}\d{1,3})-(\d{2,4})\b')

# 不锈钢牌号：SUS304/SUS302/1Cr18Ni9 等，归并到「不锈钢」实体（作为 variants/别名）
_STAINLESS_ALLOY = re.compile(r'\b(?:SUS\s?\d{2,3}|1Cr18Ni9|304|316L?|2Cr13|3Cr13)\b')

# 泛化词（不具区分度的通用词，LLM 可能误抽为实体，需过滤）
_GENERIC_STOPWORDS = {
    "连接器", "连接器设计", "设计流程", "设计", "结构", "产品", "零件",
    "塑胶", "塑膠", "金属", "塑料", "端子", "插头", "插座", "线缆",
    "connector", "design", "product", "component", "part",
}


def normalize_entities(entities: list[dict]) -> list[dict]:
    """实体规范化：
    1. 过滤泛化词（"连接器"、"设计流程"等不具区分度的通用词）
    2. 系列归并：MLG12-45 → MLG12（规格 45 记入 attributes.variants）
    """
    out = []
    series_map: dict[str, dict] = {}  # 系列名 -> {entity, variants:set}
    series_order: list[str] = []

    def _finalize_series(name: str):
        """把暂存的系列实体（带 variants）写入 out"""
        ent = series_map[name]
        if ent["variants"]:
            ent["attributes"]["variants"] = sorted(ent["variants"])
        out.append(ent)

    for e in entities:
        name = e.get("name", "").strip()
        if not name:
            continue
        # 1) 泛化词过滤
        if name.lower() in {g.lower() for g in _GENERIC_STOPWORDS}:
            continue
        # 1.5) 别名自动补全（同义归并）：按别名字典，补全 aliases
        aliases_extra = ENTITY_ALIAS_MAP.get(name, [])
        if aliases_extra:
            e["aliases"] = sorted(set((e.get("aliases") or []) + aliases_extra))
        # 2) 系列归并（仅 connector 类型）
        if e.get("type") == "connector":
            m = _SERIES_PATTERN.fullmatch(name)
            if m:
                series = m.group(1)  # 如 MLG12
                variant = m.group(2)  # 如 45
                if series not in series_map:
                    series_map[series] = {
                        **e, "name": series,
                        "aliases": list(set(e.get("aliases", []) + [series])),
                        "attributes": dict(e.get("attributes", {})),
                        "variants": {variant} if variant else set(),
                    }
                    series_order.append(series)
                else:
                    # 合并 aliases / attributes
                    existing = series_map[series]
                    existing["aliases"] = list(set(existing.get("aliases", []) + e.get("aliases", []) + [name]))
                    if e.get("description"):
                        existing["description"] = existing.get("description") or e["description"]
                    existing["variants"].add(variant)
                continue
        out.append(e)

    # 把系列实体（含 variants）追加到 out
    for series in series_order:
        _finalize_series(series)

    return _dedup(out)


# === LLM 抽取（增强）===

_LLM_SYSTEM_PROMPT = """你是工业知识库的实体抽取引擎。从给定文本中抽取实体，输出 JSON 数组。

实体类型：
- connector：连接器/型号（如 FAKRA、Mini-FAKRA、C7025）
- material：材料（如 LCP、PA66、铜合金）
- standard：标准/规范（如 GB/T、ISO）
- process：工艺/工序（如装配、镀金、压接）
- param：参数（如阻抗、镀层厚度、温度）

规则：
1. 只抽取文本中明确出现的实体，不要编造
2. name 用规范化名称，aliases 放别名
3. attributes 放结构化属性（数值、单位）
4. 直接输出 JSON 数组，不要思考过程、不要解释、不要 markdown 代码块

输出格式：
[{"name":"...","type":"...","aliases":[],"description":"","attributes":{}}]"""


def _llm_call_sync(text: str) -> str:
    """同步调用 LLM（MiMo 优先），委托 src.llm。"""
    from src.llm import call_llm_sync
    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    return call_llm_sync(messages, max_tokens=4096, prefer_mimo=True)


def extract_llm_sync(text: str) -> list[dict]:
    """同步版 LLM 抽取（供后台线程池 worker 使用，避免线程内 asyncio.run 的资源冲突）"""
    content = _llm_call_sync(text)
    entities = _parse_json_array(content)
    return _dedup(normalize_entities(entities))


def _parse_json_array(content: str) -> list[dict]:
    """从 LLM 输出中稳健地提取 JSON 数组，委托 src.llm.extract_json。"""
    from src.llm import extract_json
    if not content:
        return []
    # 先尝试直接解析整个内容（可能含 entities 包装）
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict) and isinstance(data.get("entities"), list):
            return data["entities"]
    except json.JSONDecodeError:
        pass
    data = extract_json(content, expect="array")
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def classify_standard(name: str) -> str | None:
    """标准号领域分类：根据前缀规则返回领域类别，未命中返回 None（调用方归「其他」）"""
    if not name:
        return None
    name = name.strip()
    for pat, cat in STANDARD_CATEGORY_RULES:
        if re.match(pat, name, re.IGNORECASE):
            return cat
    return None


# 标准分类的合法领域集合（规则与 LLM 共用的权威词表）
# 注意：与 STANDARD_CATEGORY_RULES 输出的领域值严格一致，禁止同义不同名
STANDARD_DOMAINS = ["材料", "紧固件", "工艺", "机械制图", "电工", "轴承", "密封件", "公差配合", "基础标准", "其他"]

# 规则置信度低的号段（宽泛区间，混有多种类别，需 LLM 精分类）
_LOW_CONFIDENCE_RANGES = [
    (2000, 6000),  # 2xxx-5xxx：材料/紧固件/工艺/制图混杂
]


def is_low_confidence_standard(name: str) -> bool:
    """判断标准号是否为规则分类低置信（落在宽泛混淆号段）"""
    m = re.match(r'^GB/T\s*(\d+)', name.strip())
    if not m:
        return False
    num = int(m.group(1))
    return any(lo <= num < hi for lo, hi in _LOW_CONFIDENCE_RANGES)


def llm_classify_standards(names: list[str]) -> dict[str, str]:
    """LLM 批量精分类：分批调用把标准号分到领域

    返回 {标准号: 领域}。对规则低置信度的号段，用 LLM 按标准名称判定真实领域。
    失败返回空 dict（调用方保留规则分类结果）。
    """
    if not names:
        return {}
    result = {}
    # 分批调用（每批 20 个），避免单次输出过长被截断
    for i in range(0, len(names), 20):
        batch = names[i:i + 20]
        result.update(_call_llm_classify_batch(batch))
    return result


def _call_llm_classify_batch(names: list[str]) -> dict[str, str]:
    """单批 LLM 标准分类（≤20 个），委托 src.llm。"""
    from src.llm import call_llm_sync, extract_json

    domains = "、".join(STANDARD_DOMAINS)
    user = (
        f"以下是 GB/T 标准号列表，请判断每个标准号所属的领域类别。\n"
        f"可选领域：{domains}\n"
        f"规则：只输出 JSON 对象，key 是标准号，value 是领域。\n"
        f"例如：{{\"GB/T 3077\": \"材料\", \"GB/T 5780\": \"紧固件\"}}\n\n"
        f"标准号列表：\n" + "\n".join(names)
    )
    messages = [
        {"role": "system", "content": "你是工业标准分类专家。请严格按照 JSON 格式输出，不要输出任何多余文字。"},
        {"role": "user", "content": user},
    ]
    content = call_llm_sync(messages, max_tokens=4096, prefer_mimo=True)
    data = extract_json(content, expect="object")
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if v in STANDARD_DOMAINS}
    return {}
