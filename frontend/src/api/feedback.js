import api from './index'

export default {
  // 记录反馈（赞/踩）
  send(kind, query, chunk_ids = [], conversation_id = null, message_id = null) {
    return api.post('/feedback', { kind, query, chunk_ids, conversation_id, message_id })
  },
  // 撤销反馈
  remove(kind, query, chunk_ids = []) {
    return api.delete('/feedback', { params: { kind, query, chunk_ids: JSON.stringify(chunk_ids) } })
  },
}
