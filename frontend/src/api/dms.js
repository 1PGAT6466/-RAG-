import api from './index'

export default {
  // 连接状态
  health() {
    return api.get('/dms/health')
  },
  // 文件夹树
  tree() {
    return api.get('/dms/tree')
  },
  // DMS 全部文档分页列表（每页默认 50，支持 status 筛选）
  documents(params) {
    return api.get('/dms/documents', { params })
  },
  // 纯文件夹列表（供上传时选择目标文件夹）
  folders() {
    return api.get('/dms/folders')
  },
  // 手动勾选导入
  importData(docIds, folderIds) {
    return api.post('/dms/import', { doc_ids: docIds, folder_ids: folderIds })
  },
  // 已导入映射列表
  records() {
    return api.get('/dms/records')
  },
  // 重置连接
  reconnect() {
    return api.post('/dms/reconnect')
  },
  // 连接配置（密码不回显明文）
  getConfig() {
    return api.get('/dms/config')
  },
  updateConfig(cfg) {
    return api.put('/dms/config', cfg)
  },
  // 检查已导入文档有无新版本
  checkUpdates() {
    return api.get('/dms/check-updates')
  },
  // 一键替换所有新版本
  replaceAll() {
    return api.post('/dms/replace-all')
  },
  // 按 file_id 反查来源
  recordByFile(fileId) {
    return api.get('/dms/record', { params: { file_id: fileId } })
  }
}
