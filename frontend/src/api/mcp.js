import api from './index'

export default {
  // 浏览市场（Smithery registry）
  market(query = '', limit = 50) {
    return api.get('/mcp/market', { params: { q: query, limit } })
  },
  // 已安装列表
  installed() {
    return api.get('/mcp/installed')
  },
  // 安装（需管理员）
  install(payload) {
    return api.post('/mcp/install', payload)
  },
  // 卸载（需管理员）
  uninstall(qualifiedName) {
    return api.post('/mcp/uninstall', { qualifiedName })
  },
  // 列出某 server 的工具
  tools(qualifiedName) {
    return api.get(`/mcp/${qualifiedName}/tools`)
  },
  // 调用工具（需管理员）
  call(qualifiedName, tool, args) {
    return api.post(`/mcp/${qualifiedName}/call`, { qualifiedName, tool, arguments: args })
  }
}
