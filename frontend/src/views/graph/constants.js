// graph/constants.js — 图谱节点的类型/颜色/标签映射（纯常量 + 纯函数）
//
// 从 GraphView.vue 抽取，消除「颜色映射散落在视图组件内」的上帝组件倾向。
// 这里只放「无状态、无响应式依赖」的映射与转换函数，供 GraphView 与其他图谱组件复用。
// Obsidian 式低饱和柔和色（灰阶为主，类型用淡彩区分），对齐工业精工设计语言。

// 实体类型 → 颜色
export const TYPE_COLORS = {
  connector: '#5e81ac',   // 柔蓝
  material: '#a3be8c',    // 柔绿
  standard: '#d08770',    // 柔橙
  process: '#b48ead',     // 柔紫
  param: '#ebcb8b',       // 柔黄
  unknown: '#8a8f98',
}

// 标准号按 standard_domain 细分着色（standard 节点的 attributes.standard_domain）
// 领域词表与后端 STANDARD_DOMAINS 严格一致 —— Obsidian 式低饱和灰阶系
export const STD_CATEGORY_COLORS = {
  '材料': '#8fa1b3',        // 蓝灰
  '紧固件': '#a3be8c',      // 草绿
  '工艺': '#b48ead',        // 柔紫
  '机械制图': '#88b0a0',    // 青灰
  '电工': '#d8a7b1',        // 粉灰
  '轴承': '#a8b88a',        // 黄绿
  '密封件': '#7d9bc1',      // 灰蓝
  '公差配合': '#d08770',    // 柔橙
  '基础标准': '#78828e',    // 中灰
  '其他': '#9aa0a8',        // 浅灰
}

export const TYPE_LABELS = {
  connector: '连接器',
  material: '材料',
  standard: '标准',
  process: '工艺',
  param: '参数',
  unknown: '未知',
}

// 文档图分类色板（按 category 落到固定色，循环取用）
export const DOC_COLORS = ['#5e81ac', '#a3be8c', '#d08770', '#b48ead', '#88b0a0', '#ebcb8b', '#d8a7b1', '#7d9bc1']

// 关系类型 → 颜色（边类型）—— Obsidian 式极淡边
export const REL_COLORS = {
  cooccur: '#4b5363',          // 暗灰：无差别共现
  spec: '#5e81ac',             // 柔蓝：规格关系
  compatible_process: '#a3be8c', // 柔绿：材料-工艺相容
  uses_standard: '#d08770',    // 柔橙：材料-标准引用
  similar: '#b48ead',          // 柔紫：文档相似
  keyword: '#5e81ac',          // 柔蓝：关键词
}

export const REL_LABELS = {
  cooccur: '共现',
  spec: '规格',
  compatible_process: '材料→工艺',
  uses_standard: '材料→标准',
  similar: '文档相似',
  keyword: '关键词',
}

export function relColor(t) { return REL_COLORS[t] || '#bfbfbf' }
export function relLabel(t) { return REL_LABELS[t] || t }

export function typeColor(t) { return TYPE_COLORS[t] || TYPE_COLORS.unknown }
export function typeLabel(t) { return TYPE_LABELS[t] || t }

// 读取 standard 节点的标准号领域（从 attributes JSON 里解 standard_domain）
export function nodeCategory(n) {
  if (!n || n.type !== 'standard') return null
  if (!n.attributes) return null
  let a = n.attributes
  if (typeof a === 'string') {
    try { a = JSON.parse(a) } catch { return null }
  }
  return a.standard_domain || null
}

// 节点实际颜色：standard 按 category 细分，其他按 type
export function nodeColor(n) {
  if (n.type === 'standard') {
    const c = nodeCategory(n)
    if (c && STD_CATEGORY_COLORS[c]) return STD_CATEGORY_COLORS[c]
  }
  return typeColor(n.type)
}

// 节点的图例显示名：standard 显示「标准·类别」，其他显示 typeLabel
export function nodeLabel(n) {
  if (n.type === 'standard') {
    const c = nodeCategory(n)
    return c ? `标准·${c}` : '标准'
  }
  return typeLabel(n.type)
}
