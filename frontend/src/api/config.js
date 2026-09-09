import api from './index'

export default {
  // 获取可管理配置清单（分组）
  list() {
    return api.get('/config')
  },
  // 更新某个配置项（写回 .env，重启生效）
  update(key, value) {
    return api.put('/config', { key, value })
  },
  // 系统健康诊断（LLM 审计 + 检索性能 profile，可观测性）
  health() {
    return api.get('/health')
  }
}
