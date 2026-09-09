import api from './index'

export default {
  // 检索命中测试：返回四路召回明细 + 融合 + rerank 后最终结果
  debug(query, top_k = 10) {
    return api.post('/search/debug', { query, top_k })
  },
  // 普通检索（复用 /api/search）
  search(query, top_k = 10) {
    return api.post('/search', { query, top_k })
  },
}
