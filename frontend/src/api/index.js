import axios from 'axios'
import router from '../router'

const api = axios.create({
  baseURL: '/api',
  timeout: 600000, // 10 分钟，支持大文件上传
})

// 请求拦截：自动带 token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：统一错误处理
api.interceptors.response.use(
  res => res,
  err => {
    const status = err.response?.status
    const data = err.response?.data

    // 401 → 清 token + 跳登录
    if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('role')
      router.push('/login')
      return Promise.reject(err)
    }

    // 后端 BizError 格式：{status:"error", error:{code, message, retryable}}
    if (data?.error?.code) {
      const { code, message, retryable } = data.error
      // 可重试错误给更友好的提示
      if (retryable) {
        err.userMessage = message || '服务暂时不可用，请稍后重试'
      } else {
        err.userMessage = message || '操作失败'
      }
      err.errorCode = code
      err.retryable = retryable
    } else if (data?.detail) {
      // 兼容旧格式：HTTPException(detail="字符串")
      err.userMessage = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } else if (status === 422) {
      // Pydantic 参数校验错误
      const fields = data?.detail
      if (Array.isArray(fields)) {
        err.userMessage = fields.map(f => f.msg || f.message || '').filter(Boolean).join('；')
      } else {
        err.userMessage = '输入参数有误，请检查'
      }
    } else if (status === 413) {
      err.userMessage = '文件过大，请压缩后重试'
    } else if (status === 429) {
      err.userMessage = '操作过于频繁，请稍后重试'
    } else if (!status) {
      err.userMessage = '网络连接失败，请检查网络'
    }

    return Promise.reject(err)
  }
)

export default api
