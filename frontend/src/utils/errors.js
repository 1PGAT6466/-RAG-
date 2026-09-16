const ERROR_MAP = {
  'Network Error': '网络连接失败，请检查网络后重试',
  'timeout': '请求超时，请稍后重试',
  'Request failed with status code 500': '服务器繁忙，请稍后重试',
  'Request failed with status code 401': '登录已过期，请重新登录',
  'Request failed with status code 403': '没有权限执行此操作',
  'Request failed with status code 404': '请求的资源不存在',
}
export function friendlyError(err) {
  const raw = err?.response?.data?.detail || err?.message || String(err)
  for (const [key, msg] of Object.entries(ERROR_MAP)) {
    if (raw.includes(key)) return msg
  }
  return raw.length > 60 ? '操作失败，请稍后重试' : raw
}
