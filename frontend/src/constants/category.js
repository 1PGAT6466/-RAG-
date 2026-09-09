/**
 * constants/category.js — 分类元数据单一权威源
 *
 * 与后端 src/classification.py 的 CATEGORY_DICT 对齐（10 类：8 工业题材 + 操作手册 + 未分类）。
 * 前端任何「分类 → 颜色 / 图标 / 标签」的映射都从这里取，避免散落硬编码。
 * 未来若后端新增分类，只需在此补一行即可全局生效。
 */
import { Link, Coin, Setting, Tools, Grid, DataAnalysis, Cpu, Document, Notebook, QuestionFilled } from '@element-plus/icons-vue'

export const CATEGORY_META = {
  '连接器':   { color: '#5e81ac', icon: Link },
  '材料选型': { color: '#a3be8c', icon: Coin },
  '工艺规程': { color: '#d08770', icon: Setting },
  '机械设计': { color: '#b48ead', icon: Tools },
  '标准件':   { color: '#7d9bc1', icon: Grid },
  '品质管理': { color: '#ebcb8b', icon: DataAnalysis },
  '电气自动化': { color: '#88b0a0', icon: Cpu },
  '外购件选型': { color: '#8fa1b3', icon: Document },
  '操作手册': { color: '#a8b88a', icon: Notebook },
  '未分类':   { color: '#9aa0a8', icon: QuestionFilled },
}

/** 分类名不匹配时的兜底色 */
export const UNKNOWN_CATEGORY_COLOR = '#9aa0a8'

/** 取某分类的颜色（未匹配回退兜底色） */
export function categoryColor(category) {
  const meta = CATEGORY_META[category]
  return meta ? meta.color : UNKNOWN_CATEGORY_COLOR
}

/** 取某分类的图标组件（未匹配回退问号图标） */
export function categoryIcon(category) {
  const meta = CATEGORY_META[category]
  return meta ? meta.icon : QuestionFilled
}

/** 分类名列表（供筛选下拉 / 图例等） */
export const CATEGORY_NAMES = Object.keys(CATEGORY_META)
