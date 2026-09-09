<template>
  <div class="debug-page">
    <div class="page-header">
      <div class="page-title">
        <el-icon class="page-title-icon"><Aim /></el-icon>
        <span>检索命中测试</span>
      </div>
      <div class="page-subtitle">RECALL DEBUG · 四路召回明细 + 融合 + Rerank 排名变化</div>
    </div>

    <!-- 查询输入行 -->
    <div class="debug-query-bar">
      <el-input
        v-model="query"
        class="debug-query-input"
        placeholder="输入查询语句，观察四路召回与 rerank 排名变化…"
        clearable
        @keyup.enter="runDebug"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button type="primary" :loading="loading" :icon="Aim" @click="runDebug">测试</el-button>
      <el-select v-model="topK" style="width:110px" @change="runDebug">
        <el-option v-for="k in [5, 10, 20, 30]" :key="k" :label="`top ${k}`" :value="k" />
      </el-select>
    </div>

    <!-- 快捷样例 -->
    <div class="debug-chips">
      <span class="debug-chip" @click="quick('FAKRA 连接器的选型要点')">FAKRA 连接器的选型要点</span>
      <span class="debug-chip" @click="quick('镀金层厚度标准')">镀金层厚度标准</span>
      <span class="debug-chip" @click="quick('磷青铜 导电率')">磷青铜 导电率</span>
      <span class="debug-chip" @click="quick('阻抗匹配是什么意思')">阻抗匹配是什么意思</span>
    </div>

    <!-- 结果概览：route + 阶段耗时 -->
    <div v-if="result" class="debug-overview">
      <div class="debug-badges">
        <span class="debug-badge debug-badge--route">路由：{{ result.route }}</span>
        <span class="debug-badge debug-badge--kind">{{ kindLabel(result.kind) }}</span>
        <span class="debug-badge debug-badge--count">最终 {{ result.final?.length ?? 0 }} 条</span>
      </div>
      <div class="debug-stages">
        <span v-for="(ms, name) in result.stages" :key="name" class="debug-stage">
          <span class="debug-stage-name">{{ name }}</span>
          <span class="debug-stage-ms">{{ ms }}ms</span>
        </span>
      </div>
    </div>

    <!-- 四路召回 + 融合 + 最终 分栏对比 -->
    <div v-if="result" class="debug-columns">
      <div class="debug-lane" v-for="lane in lanes" :key="lane.key">
        <div class="debug-lane-head">
          <span class="debug-lane-title">{{ lane.label }}</span>
          <span class="debug-lane-count">{{ lane.items.length }}</span>
        </div>
        <div class="debug-lane-body">
          <div v-if="lane.items.length === 0" class="debug-lane-empty">— 无命中 —</div>
          <div
            v-for="it in lane.items"
            :key="lane.key + '-' + it.id"
            class="debug-item"
            :title="it.content_preview"
          >
            <div class="debug-item-top">
              <span class="debug-item-score" :style="{ color: lane.scoreColor }">{{ fmtScore(it.score) }}</span>
              <span class="debug-item-file" :title="it.file_name">{{ shortName(it.file_name) }}</span>
            </div>
            <div class="debug-item-meta">
              <span v-if="it.chunk_index !== null && it.chunk_index !== undefined" class="debug-item-loc">#{{ it.chunk_index }}</span>
              <span v-if="it._bm25_rank !== undefined" class="debug-item-rank">bm25{{ it._bm25_rank }}</span>
              <span v-if="it._vector_rank !== undefined" class="debug-item-rank">vec{{ it._vector_rank }}</span>
              <span v-if="it._graph_rank !== undefined" class="debug-item-rank">graph{{ it._graph_rank }}</span>
              <span v-if="it._filename_rank !== undefined" class="debug-item-rank">fn{{ it._filename_rank }}</span>
              <span v-if="it._rerank_score !== undefined" class="debug-item-rank debug-item-rank--rerank">rr{{ fmtScore(it._rerank_score) }}</span>
            </div>
            <div class="debug-item-preview">{{ it.content_preview }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!loading" class="debug-empty">
      <div class="debug-empty-icon"><el-icon><Aim /></el-icon></div>
      <div class="debug-empty-title">输入查询，查看混合检索的完整召回链</div>
      <div class="debug-empty-sub">BM25 / 向量 / 图谱 / 文件名 四路召回 → RRF 融合 → Rerank 精排</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Aim, Search } from '@element-plus/icons-vue'
import searchApi from '../api/search'

const query = ref('')
const topK = ref(10)
const loading = ref(false)
const result = ref(null)

const kindLabel = (k) => ({ normal: '完整链路', hyde: 'HyDE 兜底', empty: '无结果' }[k] || k)

function fmtScore(s) {
  if (s === null || s === undefined) return '—'
  if (typeof s === 'number') {
    return Number.isInteger(s) ? String(s) : s.toFixed(3)
  }
  return String(s)
}

function shortName(name) {
  if (!name) return ''
  // 剥离上游 64hex 前缀（如有），取图号/文件名主体
  const cleaned = String(name).replace(/^[0-9a-f]{16,}_/, '')
  return cleaned.length > 18 ? cleaned.slice(0, 16) + '…' : cleaned
}

const lanes = computed(() => {
  if (!result.value) return []
  const r = result.value
  return [
    { key: 'bm25', label: 'BM25 召回', items: r.recalls?.bm25 || [], scoreColor: 'var(--accent)' },
    { key: 'vector', label: '向量召回', items: r.recalls?.vector || [], scoreColor: 'var(--accent-warm)' },
    { key: 'graph', label: '图谱召回', items: r.recalls?.graph || [], scoreColor: '#8b5cf6' },
    { key: 'filename', label: '文件名召回', items: r.recalls?.filename || [], scoreColor: '#d14e4e' },
    { key: 'fusion', label: '融合排序', items: r.fusion || [], scoreColor: 'var(--text-secondary)' },
    { key: 'final', label: '最终结果', items: r.final || [], scoreColor: 'var(--accent)' },
  ]
})

function quick(q) {
  query.value = q
  runDebug()
}

async function runDebug() {
  const q = query.value.trim()
  if (!q || loading.value) return
  loading.value = true
  result.value = null
  try {
    const { data } = await searchApi.debug(q, topK.value)
    result.value = data.data
  } catch (e) {
    ElMessage.error('调试失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.debug-page {
  padding: 24px 28px;
  height: 100%;
  overflow-y: auto;
}
.page-header {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}
.page-title-icon {
  color: var(--accent);
  font-size: 20px;
}
.page-subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  letter-spacing: 0.5px;
  margin-top: 4px;
}

.debug-query-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.debug-query-input {
  flex: 1;
  max-width: 620px;
}

.debug-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.debug-chip {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 5px 12px;
  cursor: pointer;
  font-family: var(--font-mono);
  transition: border-color var(--duration-fast), color var(--duration-fast);
}
.debug-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.debug-overview {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 16px;
}
.debug-badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.debug-badge {
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 6px;
  font-family: var(--font-mono);
  letter-spacing: 0.3px;
}
.debug-badge--route {
  background: var(--accent-light);
  color: var(--accent);
  font-weight: 600;
}
.debug-badge--kind {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}
.debug-badge--count {
  background: var(--accent-warm);
  color: #fff;
  opacity: 0.9;
}
.debug-stages {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.debug-stage {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px 8px;
}
.debug-stage-name {
  color: var(--text-tertiary);
}
.debug-stage-ms {
  color: var(--text-secondary);
  font-weight: 600;
}

.debug-columns {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  align-items: start;
}
@media (max-width: 1400px) {
  .debug-columns { grid-template-columns: repeat(3, 1fr); }
}
.debug-lane {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 200px;
}
.debug-lane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-subtle);
}
.debug-lane-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.3px;
}
.debug-lane-count {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}
.debug-lane-body {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 460px;
  overflow-y: auto;
}
.debug-lane-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
  padding: 24px 0;
  font-family: var(--font-mono);
}
.debug-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 8px;
  background: var(--bg-secondary);
  cursor: default;
}
.debug-item-top {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.debug-item-score {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 12px;
  flex-shrink: 0;
}
.debug-item-file {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.debug-item-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 3px;
}
.debug-item-loc {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--accent);
  background: var(--accent-glow);
  border-radius: 4px;
  padding: 0 4px;
}
.debug-item-rank {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  border-radius: 4px;
  padding: 0 4px;
}
.debug-item-rank--rerank {
  color: var(--accent-warm);
  background: var(--accent-glow);
}
.debug-item-preview {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.5;
  margin-top: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.debug-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  text-align: center;
  color: var(--text-tertiary);
}
.debug-empty-icon {
  font-size: 40px;
  color: var(--text-tertiary);
  opacity: 0.5;
  margin-bottom: 16px;
}
.debug-empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.debug-empty-sub {
  font-size: 12px;
  font-family: var(--font-mono);
}
</style>
