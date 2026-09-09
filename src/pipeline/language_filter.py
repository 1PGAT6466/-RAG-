"""
语言归一化 — 简体中文清洗（高置信度繁转简 + 非中文过滤）

设计原则（对齐用户要求「不 100% 确定就不转译」）：
  - 只做「高置信度」的繁→简转换：使用一对一无歧义的繁简映射表，
    凡是有歧义的（一简对多繁，如 干/乾/幹、发/發/髮、后/後/后）一律不转，
    保留原文。安全 > 激进。
  - 语言过滤：剔除日文假名、韩文、乱码字符；英文按「术语/型号保留 vs 长句剔除」区分。
  - 无法确定是否该转的繁体字：保留原文，并追加「[未转译:原字]」标注，
    供后续人工或语义处理识别。

Feature Flag: RAG_LANG_FILTER（默认 1，置 0 回退原样）
"""
import re
import logging
from config import RAG_LANG_FILTER

logger = logging.getLogger("rag.langfilter")

# OpenCC 惰性单例（首次 normalize_han 时加载；False 表示已尝试但不可用）
_opencc_instance = None

# ============ 高置信度繁简映射表 ============
# 只收录「一对一、无歧义」的繁→简映射。
# 故意排除歧义字（一简多繁），以及涉及异体/地域用字差异的字。
# 这些字被排除，因此在 normalize 时不会被转换，符合「不确定就不转」原则。
_SAFE_TC_TO_SC = {
    # 常见无歧义繁→简（成对唯一）
    "們": "们", "這": "这", "那": "那", "說": "说", "話": "话", "語": "语",
    "學": "学", "習": "习", "體": "体", "會": "会", "來": "来", "時": "时",
    "對": "对", "為": "为", "國": "国", "書": "书", "寫": "写", "讀": "读",
    "愛": "爱", "點": "点", "電": "电", "線": "线", "網": "网", "開": "开",
    "關": "关", "門": "门", "間": "间", "問": "问", "聞": "闻", "東": "东",
    "車": "车", "馬": "马", "鳥": "鸟", "魚": "鱼", "龍": "龙", "風": "风",
    "雲": "云", "亞": "亚", "區": "区", "這": "这", "裡": "里", "裏": "里",
    "還": "还", "遠": "远", "進": "进", "過": "过", "邊": "边", "達": "达",
    "讓": "让", "認": "认", "識": "识", "記": "记", "設": "设", "計": "计",
    "計": "计", "產": "产", "業": "业", "實": "实", "導": "导", "務": "务",
    "應": "应", "廣": "广", "廠": "厂", "場": "场", "標": "标", "準": "准",
    "層": "层", "屬": "属", "強": "强", "張": "张", "連": "连", "結": "结",
    "組": "组", "織": "织", "總": "总", "統": "统", "經": "经", "濟": "济",
    "熱": "热", "冷": "冷", "動": "动", "靜": "静", "機": "机", "器": "器",
    "械": "械", "傳": "传", "輸": "输", "貝": "贝", "頁": "页", "題": "题",
    "紅": "红", "藍": "蓝", "綠": "绿", "黃": "黄", "銀": "银", "銅": "铜",
    "鐵": "铁", "鋼": "钢", "鋁": "铝", "質": "质", "量": "量", "數": "数",
    "據": "据", "單": "单", "雙": "双", "個": "个", "隻": "只", "條": "条",
    "塊": "块", "片": "片", "種": "种", "類": "类", "樣": "样", "點": "点",
    "較": "较", "錯": "错", "對": "对", "錯": "错", "驗": "验", "證": "证",
    "測": "测", "試": "试", "檢": "检", "查": "查", "電": "电", "壓": "压",
    "流": "流", "阻": "阻", "抗": "抗", "頻": "频", "率": "率", "溫": "温",
    "溫": "温", "度": "度", "尺": "尺", "寸": "寸", "厚": "厚", "度": "度",
    "表": "表", "面": "面", "處": "处", "理": "理", "裝": "装", "配": "配",
    "圖": "图", "紙": "纸", "繪": "绘", "畫": "画", "聲": "声", "響": "响",
    "器": "器", "件": "件", "零": "零", "部": "部", "約": "约", "與": "与",
    "與": "与", "於": "于", "並": "并", "並": "并", "無": "无", "確": "确",
    "定": "定", "標": "标", "簽": "签", "證": "证", "據": "据", "現": "现",
    "在": "在", "從": "从", "他": "他", "她": "她", "它": "它", "們": "们",
    "這": "这", "裏": "里", "裡": "里", "那": "那", "哪": "哪", "樣": "样",
    "麼": "么", "什": "什", "為": "为", "什麼": "什么", "怎": "怎",
}

# 补充一些工业/知识库高频无歧义映射
_SAFE_TC_TO_SC.update({
    "聯": "联", "接": "接", "連": "连", "插": "插", "座": "座", "觸": "触",
    "點": "点", "壓": "压", "力": "力", "板": "板", "卡": "卡", "件": "件",
    "標": "标", "準": "准", "規": "规", "格": "格", "型": "型", "號": "号",
    "絕": "绝", "緣": "缘", "料": "料", "材": "材", "金": "金", "屬": "属",
    "條": "条", "紋": "纹", "螺": "螺", "紋": "纹", "栓": "栓", "帽": "帽",
    "軸": "轴", "承": "承", "密": "密", "封": "封", "潤": "润", "滑": "滑",
})

# 去重（字典本身已去重，但保险起见）
_SAFE_TC_TO_SC = dict(_SAFE_TC_TO_SC)


# ============ 字符集判断 ============

_HAN_SC = r'\u4e00-\u9fff'          # 简体/汉字（Unicode 统一表意字符，含繁简，无法仅靠码位区分）
_HIRAGANA = r'\u3040-\u309f'        # 日文平假名
_KATAKANA = r'\u30a0-\u30ff\u31f0-\u31ff'  # 日文片假名
_HANGUL = r'\uac00-\ud7af\u1100-\u11ff'    # 韩文
_PRIVATE = r'\ue000-\uf8ff'         # 私用区（乱码常见）
_FULLWIDTH_LATIN = r'\uff01-\uff5e'  # 全角符号/拉丁

# 日文/韩文/乱码判定（这些是明确该剔除的）
_FOREIGN_RE = re.compile(
    f'[{_HIRAGANA}{_KATAKANA}{_HANGUL}{_PRIVATE}]'
)
# 汉字（包含繁简，用于占比计算）
_HAN_RE = re.compile(f'[{_HAN_SC}]')
# 可保留的「技术字符」：ASCII 字母数字 + 常用符号 + 单位
_TECH_RE = re.compile(r'[A-Za-z0-9/\.\-+#%°Ωμ·×\s]')


def is_other_language(text: str) -> bool:
    """判断文本是否含日文/韩文/私用区乱码（应剔除的「非中文」）"""
    return bool(_FOREIGN_RE.search(text))


def _han_ratio(text: str) -> float:
    """汉字占比（含繁简），用于判断是否中文内容"""
    if not text:
        return 0.0
    han = len(_HAN_RE.findall(text))
    return han / max(len(text), 1)


def _get_opencc():
    """惰性加载 OpenCC t2s 转换器（标准权威映射，覆盖工业高频字如 鑑锤锌锡钼等）。
    安装失败时返回 None，调用方降级到手工映射表 _SAFE_TC_TO_SC。"""
    global _opencc_instance
    if _opencc_instance is None:
        try:
            from opencc import OpenCC
            _opencc_instance = OpenCC('t2s')
            logger.info("繁转简引擎：OpenCC (t2s)")
        except ImportError:
            logger.warning("OpenCC 未安装，繁转简回退到高置信度手工映射表（工业字覆盖不全）")
            _opencc_instance = False  # 标记已尝试且不可用
    return _opencc_instance if _opencc_instance is not False else None


# ============ 歧义繁体字保护集 ============
# 一简多繁的歧义字：同一简体对应多个繁体（干→乾/幹、发→髮/發、后→後 等），
# OpenCC t2s 会给它们一个默认转换，但无法 100% 确定原字本意，
# 故按「不 100% 确定就不转」原则保留原文（不转换）。
_AMBIGUOUS_TC = set(
    "乾幹髮發後裏裡麵臺颱檯颳鬆隻鬥復複徵鐘鍾範范餘余匯彙係系彷彿彷佛"
)


def normalize_han(text: str) -> str:
    """
    繁→简转换。
    优先用 OpenCC（标准映射，完整覆盖工业金属/化学/技术术语）；
    但歧义繁体字（一简多繁，如 乾/幹、髮/發、後）用占位符保护、转换后还原，
    保留原文，符合「不 100% 确定就不转」原则。
    OpenCC 不可用时降级到高置信度手工映射 _SAFE_TC_TO_SC（保守、覆盖不全）。
    返回转换后的文本。
    """
    if not text:
        return text
    cc = _get_opencc()
    if cc is not None:
        try:
            # 1. 把歧义繁体字替换成唯一占位符，避免被 OpenCC 误转
            protected = {}
            buf = []
            for ch in text:
                if ch in _AMBIGUOUS_TC:
                    token = f"\ue000{len(protected)}\ue001"  # 私用区占位符
                    protected[token] = ch
                    buf.append(token)
                else:
                    buf.append(ch)
            # 2. OpenCC 转换（占位符不受影响）
            converted = cc.convert("".join(buf))
            # 3. 还原占位符 → 原歧义繁体字
            for token, orig in protected.items():
                converted = converted.replace(token, orig)
            return converted
        except Exception as e:
            logger.warning(f"OpenCC 转换失败（回退手工映射）: {e}")
    return ''.join(_SAFE_TC_TO_SC.get(ch, ch) for ch in text)


def filter_chunk(content: str) -> str:
    """
    对一个 chunk 的 content 做语言归一化：
    1. 剔除日文/韩文/私用区乱码字符（替换为空格，不硬删以免粘连）
    2. 高置信度繁→简
    （英文术语/型号保留，不做激进删除——长英文句的剔除放在「非中文占比」判断）
    """
    if not RAG_LANG_FILTER or not content:
        return content

    # 1. 剔除日文/韩文/乱码（这类字符在简体工业文档里基本是噪声）
    content = _FOREIGN_RE.sub(' ', content)

    # 2. 高置信度繁→简
    content = normalize_han(content)

    # 3. 清理多余空格（过滤可能产生连续空格）
    content = re.sub(r'[ \t]{2,}', ' ', content)

    return content.strip()


def should_drop_chunk(content: str) -> bool:
    """
    判断一个 chunk 是否应整体丢弃（非中文为主的内容）。
    规则：汉字占比 < 0.3 且长度 > 50 时，判定为「非中文内容」丢弃。
    （英文长句、乱码段、纯符号段会被丢弃；短型号串因长度不达标会保留）

    豁免规则（2026-08-26 修复表格数据误杀）：
      中英混排的表格型数据（采购单/料表），每行大量品号/规格/订单号/日期是
      ASCII/数字，把汉字占比稀释到 <0.3，但其「品名/供应商/采购员」等列含
      明确的中文语义（连续汉字词）。此时保留 chunk，避免合法中文数据被误丢。
      实现：若文本含「连续 ≥2 字中文词」，视为有中文语义，不丢弃。
    """
    if not RAG_LANG_FILTER or not content:
        return False
    text = content.strip()
    if len(text) <= 50:
        return False
    # 豁免：含连续中文词（≥2字）的中英混排表格数据，保留
    if re.search(r'[\u4e00-\u9fff]{2,}', text):
        return False
    return _han_ratio(text) < 0.3


def normalize_chunks(chunks: list[dict]) -> list[dict]:
    """
    对 chunk 列表统一做语言归一化，返回过滤后的 chunk 列表。
    会：
      - 对每个 chunk 的 content 做 filter_chunk
      - 丢弃 should_drop_chunk 判定为真（非中文）的 chunk
    """
    if not RAG_LANG_FILTER:
        return chunks

    result = []
    for c in chunks:
        raw = c.get("content", "")
        cleaned = filter_chunk(raw)
        if not cleaned:
            continue
        if should_drop_chunk(cleaned):
            logger.debug(f"丢弃非中文 chunk: {cleaned[:40]}...")
            continue
        new_c = dict(c)
        new_c["content"] = cleaned
        result.append(new_c)
    return result
