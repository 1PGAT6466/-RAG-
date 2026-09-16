"""
清洗回归测试（W4）
==================
读取 data/eval/cleaning_samples/*.json，执行 clean_chunks 断言，
验证清洗逻辑不会误杀有效内容。

用法：
  python scripts/eval_cleaner.py                    # 运行所有样本
  python scripts/eval_cleaner.py --verbose          # 详细输出
"""
import sys
import json
import glob
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_samples():
    """加载所有清洗样本"""
    sample_dir = Path(__file__).resolve().parent.parent / "data" / "eval" / "cleaning_samples"
    samples = []
    for f in sorted(glob.glob(str(sample_dir / "*.json"))):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
            data["_file"] = Path(f).name
            samples.append(data)
    return samples


def run_sample(sample: dict, verbose: bool = False) -> dict:
    """运行单个样本的断言"""
    name = sample.get("name", "unknown")
    stype = sample.get("type", "")
    inp = sample.get("input", [])
    expected = sample.get("expected", {})
    errors = []

    if stype == "clean_chunks":
        from src.pipeline.chunker import clean_chunks
        output = clean_chunks(inp)
        out_count = len(output)
        min_count = expected.get("min_output_count", 0)
        must_contain = expected.get("must_contain", [])
        output_gt_input = expected.get("output_gt_input", False)

        # 断言 1: 输出数量 >= 最小期望
        if out_count < min_count:
            errors.append(f"输出数量 {out_count} < 期望最小 {min_count}")

        # 断言 2: 必须包含的关键词
        all_text = " ".join(c.get("content", "") for c in output)
        for kw in must_contain:
            if kw not in all_text:
                errors.append(f"缺少必须关键词: '{kw}'")

        # 断言 3: 输出 > 输入（BOM 表不应被过滤）
        if output_gt_input and out_count < len(inp):
            errors.append(f"输出数量 {out_count} < 输入数量 {len(inp)}（不应被过滤）")

        if verbose:
            print(f"  输入: {len(inp)} 条, 输出: {out_count} 条")
            for kw in must_contain:
                status = "✓" if kw in all_text else "✗"
                print(f"  {status} 关键词 '{kw}'")

    elif stype == "language_filter":
        from src.pipeline.chunker import _filter_non_chinese_chunks
        output = _filter_non_chinese_chunks(inp)
        out_count = len(output)
        min_count = expected.get("min_output_count", 0)
        max_count = expected.get("max_output_count", 999)
        must_keep = expected.get("must_keep_indices", [])
        must_drop = expected.get("must_drop_indices", [])

        if out_count < min_count:
            errors.append(f"输出数量 {out_count} < 期望最小 {min_count}")
        if out_count > max_count:
            errors.append(f"输出数量 {out_count} > 期望最大 {max_count}")

        out_indices = {c.get("index") for c in output}
        for idx in must_keep:
            if idx not in out_indices:
                errors.append(f"应保留的 index={idx} 被误删")
        for idx in must_drop:
            if idx in out_indices:
                errors.append(f"应删除的 index={idx} 被保留")

        if verbose:
            print(f"  输入: {len(inp)} 条, 输出: {out_count} 条")
            print(f"  保留: {sorted(out_indices)}")

    elif stype == "normalize":
        from src.pipeline.language_filter import normalize_han
        must_contain = expected.get("must_contain", [])
        must_not_contain = expected.get("must_not_contain", [])
        for chunk in inp:
            text = chunk.get("content", "")
            normalized = normalize_han(text)
            if verbose:
                print(f"  输入: {text[:40]}...")
                print(f"  输出: {normalized[:40]}...")
            for kw in must_contain:
                if kw not in normalized:
                    errors.append(f"规范化后缺少关键词: '{kw}'")
                elif verbose:
                    print(f"  ✓ 包含 '{kw}'")
            for kw in must_not_contain:
                if kw in normalized:
                    errors.append(f"规范化后仍含繁体: '{kw}'")
                elif verbose:
                    print(f"  ✓ 不含 '{kw}'")

    elif stype == "quality":
        from src.pipeline.quality import compute_quality_score
        text = sample.get("input_text", "")
        min_score = expected.get("min_score", 0)
        score = compute_quality_score(text)
        if verbose:
            print(f"  质量分: {score} (期望 >= {min_score})")
        if score < min_score:
            errors.append(f"质量分 {score} < 期望最小 {min_score}")

    else:
        errors.append(f"未知样本类型: {stype}")

    return {
        "name": name,
        "file": sample.get("_file", ""),
        "passed": len(errors) == 0,
        "errors": errors,
    }


def main():
    ap = argparse.ArgumentParser(description="清洗回归测试")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    samples = load_samples()
    if not samples:
        print("未找到任何清洗样本")
        sys.exit(1)

    print(f"加载 {len(samples)} 个清洗样本\n")
    passed = 0
    failed = 0

    for s in samples:
        result = run_sample(s, verbose=args.verbose)
        status = "✓ PASS" if result["passed"] else "✗ FAIL"
        print(f"[{status}] {result['name']} ({result['file']})")
        if not result["passed"]:
            failed += 1
            for e in result["errors"]:
                print(f"  ✗ {e}")
        else:
            passed += 1

    print(f"\n{'='*50}")
    print(f"结果: {passed} passed, {failed} failed, 共 {len(samples)} 条")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
