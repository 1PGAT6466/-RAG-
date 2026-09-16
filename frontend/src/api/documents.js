import api from './index'

// 回收站
export const recycleBin = () => api.get('/documents/recycle-bin')
export const restoreFile = (fileId) => api.post(`/documents/${fileId}/restore`)
export const permanentDeleteFile = (fileId) => api.delete(`/documents/${fileId}/permanent`)

// 审计日志
export const getAuditLogs = (params) => api.get('/audit', { params })

// 文件权限
export const getFilePermissions = (fileId) => api.get(`/documents/${fileId}/permissions`)
export const setFilePermission = (fileId, data) => api.post(`/documents/${fileId}/permissions`, data)

// 文档操作
export const deleteFile = (fileId) => api.delete(`/documents/${fileId}`)
export const getDocuments = (params) => api.get('/documents', { params })
export const getDocument = (fileId) => api.get(`/documents/${fileId}`)
export const uploadDocument = (formData, onProgress) =>
  api.post('/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress,
  })
