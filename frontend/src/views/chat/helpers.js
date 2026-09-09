/**
 * ChatView 辅助函数（纯函数，零响应式依赖）
 */

/**
 * 格式化时间戳为 HH:MM
 */
export function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/**
 * 从消息 sources 提取 chunk_ids（供反馈落库）
 */
export function extractChunkIds(msg) {
  const ids = []
  for (const s of (msg.sources || [])) {
    if (s.chunk_id !== undefined && s.chunk_id !== null) ids.push(s.chunk_id)
    else if (s.id !== undefined && s.id !== null) ids.push(s.id)
  }
  return ids
}

/**
 * textarea 自动增高（上限 180px）
 */
export function autoGrow(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

/**
 * 从响应中提取 token（兼容嵌套格式）
 */
export function extractToken(resp) {
  if (resp && typeof resp === 'object') {
    if (resp.data && typeof resp.data === 'object' && resp.data.token) return resp.data.token
    if (resp.token) return resp.token
  }
  return ''
}
