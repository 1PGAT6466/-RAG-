"""pytest 全局配置 — 统一路径

项目用 `src` 包结构，测试需把项目根加入 sys.path（绝对导入 from src.xxx / import config）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
