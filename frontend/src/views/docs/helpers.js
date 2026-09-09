/**
 * DocumentsView 辅助函数（纯函数，零响应式依赖）
 */

/**
 * 格式化 SQLite datetime 为 YYYY-MM-DD HH:MM
 */
export function formatDate(ts) {
  if (!ts) return ''
  const s = String(ts)
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 16)
  const d = new Date(ts)
  if (isNaN(d)) return s
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/**
 * 文件扩展名 → emoji 图标
 */
export function iconFor(ext) {
  const map = { '.pdf': '📄', '.ppt': '📊', '.pptx': '📊', '.xlsx': '📈', '.xls': '📈', '.docx': '📝', '.doc': '📝' }
  return map[ext?.toLowerCase()] || '📋'
}

/**
 * 扩展名 → 等宽徽标文字（工业精工风格）
 */
export function extLabel(ext) {
  const e = (ext || '').replace('.', '').toUpperCase()
  const short = { PPTX: 'PPT', XLSX: 'XLS', XLS: 'XLS', DOCX: 'DOC', DOC: 'DOC' }
  return short[e] || e || 'FILE'
}

/**
 * 扩展名徽标样式（中性冷灰底 + 深青蓝文字）
 */
export function iconStyle() {
  return { background: 'var(--bg-tertiary)', color: 'var(--accent)' }
}

/**
 * 格式化文件大小
 */
export function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

/**
 * 向量化任务状态文字
 */
export function jobStatusText(job) {
  if (!job) return ''
  if (job.status === 'done') return '已完成'
  if (job.status === 'failed') return '失败'
  if (job.progress_text) return job.progress_text
  return `${job.progress || 0}%`
}

/**
 * 格式化 DMS 文件夹路径标签
 */
export function formatDmsFolderLabel(f) {
  if (!f) return ''
  return f.path || f.name || `folder-${f.id}`
}

/**
 * 任务条目样式类
 */
export function jobStatusClass(job) {
  if (job.status === 'done') return 'status-done'
  if (job.status === 'failed') return 'status-failed'
  return 'status-running'
}
