/**
 * GraphView 工具函数（纯函数，零响应式依赖）
 */
export function safeParse(s) {
  try { return JSON.parse(s) } catch { return {} }
}

export function specValue(row) {
  const a = row.attributes || {}
  const v = a.value ?? a.数值 ?? ''
  const u = a.unit ?? a.单位 ?? ''
  return v ? `${v} ${u}`.trim() : ''
}

export function prettyAttr(attr) {
  if (!attr) return ''
  if (typeof attr === 'string') {
    try { return JSON.stringify(JSON.parse(attr), null, 2) } catch { return attr }
  }
  return JSON.stringify(attr, null, 2)
}

export function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '...' : s
}

/**
 * 构建图例（文档模式：按 category 分组）
 */
export function buildDocLegend(nodes) {
  const cats = {}
  for (const n of nodes) {
    const cat = n.category || '未分类'
    cats[cat] = (cats[cat] || 0) + 1
  }
  return Object.entries(cats).map(([name, count]) => ({ name, count }))
}

/**
 * 构建图例（实体模式：按标准化类别分组）
 */
export function buildEntityLegend(nodes, nodeCategory) {
  const cats = {}
  for (const n of nodes) {
    const c = nodeCategory(n) || '未分类'
    const key = 'std:' + c
    cats[key] = (cats[key] || 0) + 1
    const t = n.type || 'unknown'
    cats[t] = (cats[t] || 0) + 1
  }
  return Object.entries(cats)
    .filter(([name]) => name.startsWith('std:'))
    .map(([name, count]) => ({ name: name.slice(4), count }))
}

/**
 * 构建边图例（按 rel_type 分组）
 */
export function buildEdgeLegend(edges) {
  const cats = {}
  for (const e of edges) {
    const t = e.rel_type || 'cooccur'
    cats[t] = (cats[t] || 0) + 1
  }
  return Object.entries(cats).map(([name, count]) => ({ name, count }))
}
