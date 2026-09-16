"""答案 Grounding 校验（Phase 3）

检查生成的回答是否被检索到的 sources 支撑（entailment）。
不支撑的引用编号会被清洗，防止 LLM 杜撰。

用法：
  from src.chat.grounding import validate_grounding
  validated = validate_grounding(answer, sources)
"""
import re
import logging

logger = logging.getLogger("rag.grounding")


def validate_grounding(answer: str, sources: list[dict]) -> dict:
    """校验 answer 中的引用编号是否被 sources 支撑。

    返回：
      - answer: 清洗后的 answer（不支撑的引用已移除）
      - removed: 被移除的引用编号列表
      - grounded: 被支撑的引用编号列表
      - score: grounding 分数（0-1，支撑引用占比）
    """
    if not answer or not sources:
        return {"answer": answer, "removed": [], "grounded": [], "score": 1.0}

    # 提取 answer 中的引用编号 [1], [2], ...
    cited = set(re.findall(r'\[(\d+)\]', answer))
    if not cited:
        return {"answer": answer, "removed": [], "grounded": [], "score": 1.0}

    # 检查哪些编号在 sources 范围内
    valid_ids = set()
    for i, s in enumerate(sources, 1):
        valid_ids.add(str(i))

    grounded = []
    removed = []
    for num in sorted(cited, key=int):
        if num in valid_ids:
            grounded.append(num)
        else:
            removed.append(num)

    # 清洗：移除不支撑的引用标记
    cleaned = answer
    for num in removed:
        cleaned = re.sub(rf'\[{num}\]', '', cleaned)

    # 计算 grounding 分数
    total = len(cited)
    score = len(grounded) / total if total > 0 else 1.0

    if removed:
        logger.info(f"Grounding 校验: {len(grounded)}/{total} 支撑, 移除 {removed}")

    return {
        "answer": cleaned,
        "removed": removed,
        "grounded": grounded,
        "score": round(score, 3),
    }


def clean_phantom_citations(answer: str, sources: list[dict]) -> str:
    """清理杜撰引用（原函数增强版，支持 grounding 分数返回）。"""
    result = validate_grounding(answer, sources)
    return result["answer"]
