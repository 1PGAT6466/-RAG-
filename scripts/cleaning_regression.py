"""清洗样本库回归测试

用途：清洗规则改动后，必须跑此脚本确认不回归。
样本格式：每个样本是一个 JSON 文件，包含 input + expected。
运行：python scripts/cleaning_regression.py
输出：回归报告（控制台 + data/eval/cleaning_regression_report.json）

设计原则：
- 样本来自真实文档的典型问题（不是人造数据）
- 每个样本有明确的「预期输出」（不依赖随机性）
- 新增样本必须附带说明（为什么要加、测的是什么场景）
"""
import json
import os
import sys
import time
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES_DIR = ROOT / "data" / "eval" / "cleaning_samples"
REPORT_PATH = ROOT / "data" / "eval" / "cleaning_regression_report.json"


def load_samples() -> list[dict]:
    """加载所有样本文件"""
    samples = []
    if not SAMPLES_DIR.exists():
        return samples
    for f in sorted(SAMPLES_DIR.glob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            data["_file"] = f.name
            samples.append(data)
    return samples


def run_clean_chunks(chunks: list[dict]) -> list[dict]:
    """调用真实的 clean_chunks 函数"""
    from src.pipeline.chunker import clean_chunks
    return clean_chunks(chunks)


def run_normalize_chunks(chunks: list[dict]) -> list[dict]:
    """调用语言归一化"""
    from src.pipeline.language_filter import normalize_chunks
    return normalize_chunks(chunks)


def evaluate_sample(sample: dict) -> dict:
    """评估单个样本，返回结果 dict"""
    name = sample.get("name", sample.get("_file", "unknown"))
    desc = sample.get("description", "")
    test_type = sample.get("type", "clean_chunks")  # clean_chunks | normalize | quality
    input_chunks = sample.get("input", [])
    expected = sample.get("expected", {})
    
    result = {
        "name": name,
        "description": desc,
        "type": test_type,
        "file": sample.get("_file", ""),
        "pass": False,
        "details": {},
        "error": None,
    }

    try:
        if test_type == "clean_chunks":
            output = run_clean_chunks(input_chunks)
            result["details"]["output_count"] = len(output)
            result["details"]["input_count"] = len(input_chunks)
            
            # 检查预期
            checks = []
            if "min_output_count" in expected:
                ok = len(output) >= expected["min_output_count"]
                checks.append(("min_output_count", ok, f"{len(output)} >= {expected['min_output_count']}"))
            if "max_output_count" in expected:
                ok = len(output) <= expected["max_output_count"]
                checks.append(("max_output_count", ok, f"{len(output)} <= {expected['max_output_count']}"))
            if "exact_count" in expected:
                ok = len(output) == expected["exact_count"]
                checks.append(("exact_count", ok, f"{len(output)} == {expected['exact_count']}"))
            if "must_contain" in expected:
                all_text = " ".join(c.get("content", "") for c in output)
                for term in expected["must_contain"]:
                    ok = term in all_text
                    checks.append((f"must_contain:{term}", ok, f"'{term}' in output"))
            if "must_not_contain" in expected:
                all_text = " ".join(c.get("content", "") for c in output)
                for term in expected["must_not_contain"]:
                    ok = term not in all_text
                    checks.append((f"must_not_contain:{term}", ok, f"'{term}' not in output"))
            if "output_gt_input" in expected and expected["output_gt_input"]:
                ok = len(output) >= len(input_chunks)
                checks.append(("output_gt_input", ok, f"{len(output)} >= {len(input_chunks)}"))
            
            result["details"]["checks"] = [
                {"name": c[0], "pass": c[1], "info": c[2]} for c in checks
            ]
            result["pass"] = all(c[1] for c in checks) if checks else True

        elif test_type == "normalize":
            output = run_normalize_chunks(input_chunks)
            result["details"]["output_count"] = len(output)
            result["details"]["input_count"] = len(input_chunks)
            
            checks = []
            if "must_contain" in expected:
                all_text = " ".join(c.get("content", "") for c in output)
                for term in expected["must_contain"]:
                    ok = term in all_text
                    checks.append((f"must_contain:{term}", ok, f"'{term}' in output"))
            if "must_not_contain" in expected:
                all_text = " ".join(c.get("content", "") for c in output)
                for term in expected["must_not_contain"]:
                    ok = term not in all_text
                    checks.append((f"must_not_contain:{term}", ok, f"'{term}' not in output"))
            if "exact_count" in expected:
                ok = len(output) == expected["exact_count"]
                checks.append(("exact_count", ok, f"{len(output)} == {expected['exact_count']}"))
            
            result["details"]["checks"] = [
                {"name": c[0], "pass": c[1], "info": c[2]} for c in checks
            ]
            result["pass"] = all(c[1] for c in checks) if checks else True

        elif test_type == "quality":
            from src.pipeline.quality import compute_quality_score
            text = sample.get("input_text", "")
            qs = compute_quality_score(text)
            result["details"]["quality_score"] = qs
            
            checks = []
            if "min_score" in expected:
                ok = qs >= expected["min_score"]
                checks.append(("min_score", ok, f"{qs} >= {expected['min_score']}"))
            if "max_score" in expected:
                ok = qs <= expected["max_score"]
                checks.append(("max_score", ok, f"{qs} <= {expected['max_score']}"))
            
            result["details"]["checks"] = [
                {"name": c[0], "pass": c[1], "info": c[2]} for c in checks
            ]
            result["pass"] = all(c[1] for c in checks) if checks else True

    except Exception as e:
        result["error"] = str(e)
        result["pass"] = False

    return result


def run_regression() -> dict:
    """运行全部回归测试，返回报告"""
    samples = load_samples()
    if not samples:
        return {
            "status": "skip",
            "message": "无样本文件",
            "samples_dir": str(SAMPLES_DIR),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    results = []
    passed = 0
    failed = 0
    errors = 0

    for sample in samples:
        r = evaluate_sample(sample)
        results.append(r)
        if r["error"]:
            errors += 1
        elif r["pass"]:
            passed += 1
        else:
            failed += 1

    report = {
        "status": "fail" if (failed + errors) > 0 else "pass",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(samples),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "results": results,
    }

    # 保存报告
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def print_report(report: dict):
    """打印回归报告"""
    status = report["status"]
    icon = "✅" if status == "pass" else "❌"
    print(f"\n{icon} 清洗回归测试 {status.upper()}")
    print(f"   时间: {report['timestamp']}")
    print(f"   总计: {report['total']} | 通过: {report['passed']} | 失败: {report['failed']} | 错误: {report['errors']}")
    
    if report.get("results"):
        print(f"\n{'='*60}")
        for r in report["results"]:
            icon = "✅" if r["pass"] else ("💥" if r["error"] else "❌")
            print(f"  {icon} {r['name']}: {r['description']}")
            if r["error"]:
                print(f"     ERROR: {r['error']}")
            for c in r.get("details", {}).get("checks", []):
                ci = "✅" if c["pass"] else "❌"
                print(f"     {ci} {c['name']}: {c['info']}")
    
    report_path = REPORT_PATH if REPORT_PATH.exists() else "未保存"
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    report = run_regression()
    print_report(report)
    sys.exit(0 if report["status"] == "pass" else 1)
