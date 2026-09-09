"""
ingest.py — 入库工具函数（已迁移至 classification.py）

本模块的 _auto_classify 和 _auto_folder 已迁移至 src.classification：
  - classify_document(filename, text)  — 文档自动分类
  - detect_document_folder(filename, text) — 发行系统文件夹识别

保留此文件仅为向后兼容，新代码请直接 import src.classification。
"""
from src.classification import classify_document as _auto_classify
from src.classification import detect_document_folder as _auto_folder

__all__ = ["_auto_classify", "_auto_folder"]
