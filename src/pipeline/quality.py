"""
quality.py — 文档清洗质量评分（P3）
===================================

解析后计算质量分（0-100），写入 files.quality_score。
评分维度：
  1. 有效 token 率（40分）：非空中文/英文字符占比
  2. 乱码率（30分）：jieba 分词多字词命中率（正常 ≥0.55，乱码 ≤0.45）
  3. 结构完整度（20分）：是否有标题/表格/段落结构
  4. 长度合理性（10分）：过短/过长扣分

质量等级：
  ≥80: 优质（绿色）
  60-79: 良好（黄色）
  40-59: 一般（橙色）
  <40: 低质量（红色）
  -1: 未评分（默认值）
"""
import re
import logging

logger = logging.getLogger("rag.quality")


def compute_quality_score(text: str) -> int:
    """计算文档质量分（0-100）。

    输入：解析后的纯文本（parse_file 产出）
    输出：0-100 整数质量分
    """
    if not text or not text.strip():
        return 0

    score = 0

    # 1. 有效 token 率（40分）
    score += _score_token_quality(text)

    # 2. 乱码率（30分）
    score += _score_garble_rate(text)

    # 3. 结构完整度（20分）
    score += _score_structure(text)

    # 4. 长度合理性（10分）
    score += _score_length(text)

    return min(100, max(0, score))


def _score_token_quality(text: str) -> int:
    """有效 token 率（40分）：非空中文/英文字符占比"""
    total = len(text)
    if total == 0:
        return 0
    # 有效字符：中文、英文、数字、常见标点
    valid = len(re.findall(r'[\u4e00-\u9fff\w]', text))
    ratio = valid / total
    # 正常技术文档 ≥0.6
    if ratio >= 0.8:
        return 40
    elif ratio >= 0.6:
        return 30
    elif ratio >= 0.4:
        return 20
    elif ratio >= 0.2:
        return 10
    return 0


def _score_garble_rate(text: str) -> int:
    """乱码率（30分）：jieba 分词多字词命中率"""
    try:
        import jieba
        words = list(jieba.cut(text[:5000]))  # 取前 5000 字采样
        if not words:
            return 15  # 无法判断，给中间分
        # 多字词（长度≥2）占比
        multi = sum(1 for w in words if len(w) >= 2 and w.strip())
        ratio = multi / max(len(words), 1)
        # 正常中文文档 ≥0.55
        if ratio >= 0.6:
            return 30
        elif ratio >= 0.5:
            return 25
        elif ratio >= 0.4:
            return 15
        elif ratio >= 0.3:
            return 5
        return 0
    except ImportError:
        # jieba 不可用，跳过此项，给中间分
        return 15


def _score_structure(text: str) -> int:
    """结构完整度（20分）：是否有标题/表格/段落结构"""
    score = 0
    # 标题检测：数字编号标题（1. / 1.1 / 一、 / 第X章）
    headings = len(re.findall(r'(?:^|\n)\s*(?:\d+\.)+\d*\s+\S|(?:^|\n)\s*[一二三四五六七八九十]+[、.]\s*\S|(?:^|\n)\s*第.{1,5}[章节]', text))
    if headings >= 3:
        score += 8
    elif headings >= 1:
        score += 4

    # 表格检测：markdown 表格或制表符分隔
    tables = len(re.findall(r'\|.*\|.*\|', text))
    if tables >= 3:
        score += 6
    elif tables >= 1:
        score += 3

    # 段落分隔：连续换行分段
    paragraphs = len(re.split(r'\n\s*\n', text))
    if paragraphs >= 5:
        score += 6
    elif paragraphs >= 2:
        score += 3

    return score


def _score_length(text: str) -> int:
    """长度合理性（10分）：过短/过长扣分"""
    length = len(text.strip())
    if length < 100:
        return 0  # 过短，可能解析失败
    elif length < 500:
        return 5  # 偏短
    elif length <= 500000:
        return 10  # 正常范围
    else:
        return 7  # 超长（可能是 BOM 表全量导入，扣少量分）


def quality_label(score: int) -> str:
    """质量等级标签"""
    if score < 0:
        return "未评分"
    elif score >= 80:
        return "优质"
    elif score >= 60:
        return "良好"
    elif score >= 40:
        return "一般"
    else:
        return "低质量"


def quality_color(score: int) -> str:
    """质量等级颜色（前端用）"""
    if score < 0:
        return "#909399"  # 灰色
    elif score >= 80:
        return "#67c23a"  # 绿色
    elif score >= 60:
        return "#e6a23c"  # 黄色
    elif score >= 40:
        return "#f56c6c"  # 橙色
    else:
        return "#f56c6c"  # 红色
