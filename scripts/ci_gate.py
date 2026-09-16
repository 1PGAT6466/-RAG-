"""CI 门禁脚本：组合测试 + 检索评测

用途：push 前或 CI 中运行，确认核心功能不回归。
退出码：0=全部通过，1=有失败

测试内容：
1. pytest 单元测试（tests/）
2. 清洗回归测试（scripts/cleaning_regression.py）
3. 检索评测（scripts/retrieval_benchmark.py，可选）
4. 前端构建（npm run build，可选）

用法：
  python scripts/ci_gate.py                    # 仅 pytest + 清洗回归
  python scripts/ci_gate.py --full             # 全量（含检索评测 + 前端构建）
  python scripts/ci_gate.py --skip-retrieval   # 跳过检索评测
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run_step(name: str, cmd: list[str], cwd: str = None, timeout: int = 300) -> bool:
    """运行一个测试步骤，返回是否通过"""
    print(f"\n{'='*60}")
    print(f"  🔍 {name}")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - start
        
        if result.returncode == 0:
            print(f"  ✅ {name} 通过 ({elapsed:.1f}s)")
            # 只打印最后 10 行
            lines = result.stdout.strip().split("\n")
            for line in lines[-10:]:
                print(f"     {line}")
            return True
        else:
            print(f"  ❌ {name} 失败 ({elapsed:.1f}s)")
            print(f"  stdout: {result.stdout[-500:]}")
            print(f"  stderr: {result.stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"  ⏰ {name} 超时 ({timeout}s)")
        return False
    except Exception as e:
        print(f"  💥 {name} 异常: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="全量测试（含检索评测+前端构建）")
    parser.add_argument("--skip-retrieval", action="store_true", help="跳过检索评测")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建")
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  🚀 CI 门禁测试")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'全量' if args.full else '标准'}")
    print(f"{'='*60}")
    
    results = {}
    start_total = time.time()
    
    # Step 1: pytest 单元测试
    results["pytest"] = run_step(
        "pytest 单元测试",
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no", "-x"],
        timeout=120,
    )
    
    # Step 2: 清洗回归测试
    results["cleaning"] = run_step(
        "清洗回归测试",
        [sys.executable, "scripts/cleaning_regression.py"],
        timeout=60,
    )
    
    # Step 3: 检索评测（可选）
    if args.full and not args.skip_retrieval:
        results["retrieval"] = run_step(
            "检索评测",
            [sys.executable, "scripts/retrieval_benchmark.py"],
            timeout=120,
        )
    
    # Step 4: 前端构建（可选）
    if args.full and not args.skip_frontend:
        frontend_dir = ROOT / "frontend"
        if frontend_dir.exists():
            results["frontend"] = run_step(
                "前端构建",
                ["npm", "run", "build"],
                cwd=str(frontend_dir),
                timeout=120,
            )
    
    # 汇总
    elapsed_total = time.time() - start_total
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    
    print(f"\n{'='*60}")
    if failed == 0:
        print(f"  ✅ CI 门禁 PASS")
    else:
        print(f"  ❌ CI 门禁 FAIL")
    print(f"  总计: {len(results)} | 通过: {passed} | 失败: {failed}")
    print(f"  耗时: {elapsed_total:.1f}s")
    print(f"{'='*60}")
    
    for name, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
