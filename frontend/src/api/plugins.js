import api from './index'

export default {
  // 插件列表
  list() {
    return api.get('/plugins')
  },
  // 插件详情
  get(name) {
    return api.get(`/plugins/${name}`)
  },
  // 启用
  enable(name) {
    return api.post(`/plugins/${name}/enable`)
  },
  // 停用
  disable(name) {
    return api.post(`/plugins/${name}/disable`)
  },
  // 卸载
  uninstall(name) {
    return api.post(`/plugins/${name}/uninstall`)
  },
  // 调用插件
  invoke(name, method, params) {
    return api.post(`/plugins/${name}/invoke`, { method, params })
  },
  // 运行状态
  status(name) {
    return api.get(`/plugins/${name}/status`)
  }
}
