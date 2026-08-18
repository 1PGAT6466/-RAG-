<template>
  <div class="graph-page">
    <!-- 顶部 Tab 切换：双图谱 -->
    <div class="graph-tabs">
      <el-radio-group v-model="mode" size="large">
        <el-radio-button value="document">文档引用图</el-radio-button>
        <el-radio-button value="entity">实体关系图</el-radio-button>
      </el-radio-group>

      <div class="entity-filter" v-if="mode === 'entity'">
        <el-select v-model="activeTypes" multiple collapse-tags placeholder="按实体类型/标准类别筛选" style="width: 320px">
          <el-option v-for="t in typeFilter" :key="t.name" :label="`${t.label} (${t.count})`" :value="t.name" />
        </el-select>
        <el-select v-model="activeRelTypes" multiple collapse-tags placeholder="按关系类型筛选" style="width: 280px">
          <el-option v-for="r in relTypeFilter" :key="r.name" :label="`${r.label} (${r.count})`" :value="r.name" />
        </el-select>
      </div>

      <el-checkbox v-model="showLabels" style="margin-left:auto">显示标签</el-checkbox>
    </div>

    <!-- 图谱画布 -->
    <div class="graph-container" ref="container">
      <svg ref="svgEl"></svg>
      <div v-if="graphTruncated" class="graph-truncated-hint">
        节点过多，已按关联度显示 Top {{ MAX_GRAPH_NODES }} 核心节点，可用上方筛选缩小范围
      </div>
      <div v-if="loading" class="loading-center">
        <el-icon class="is-loading" style="font-size:32px"><Loading /></el-icon>
      </div>
      <div v-if="!loading && nodes.length === 0" class="empty-state" style="position:absolute;inset:0">
        <el-icon><Connection /></el-icon>
        <p>{{ mode === 'document' ? '暂无文档图谱' : '暂无实体图谱' }}</p>
        <p style="font-size:12px">{{ mode === 'document' ? '上传文档后自动建立关联' : '入库后自动抽取实体与关系' }}</p>
      </div>
      <div class="graph-legend" v-if="nodes.length > 0">
        <div style="font-weight:600;margin-bottom:8px">节点</div>
        <div v-for="cat in legend" :key="cat.name" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
          <div :style="{ width:'10px', height:'10px', borderRadius:'50%', background: cat.color }"></div>
          <span>{{ cat.name }}</span>
          <span style="color:var(--text-tertiary)">({{ cat.count }})</span>
        </div>
        <template v-if="mode === 'entity' && edgeLegend.length > 0">
          <div style="font-weight:600;margin:12px 0 8px">关系</div>
          <div v-for="cat in edgeLegend" :key="cat.name" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <div :style="{ width:'16px', height:'2px', background: cat.color }"></div>
            <span>{{ cat.label }}</span>
            <span style="color:var(--text-tertiary)">({{ cat.count }})</span>
          </div>
        </template>
      </div>
    </div>

    <!-- 反链面板（点击实体/文档后展示） -->
    <el-drawer v-model="drawerVisible" :title="drawerTitle" size="380px">
      <div v-if="selectedEntity" class="entity-detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="类型">{{ typeLabel(selectedEntity.type) }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedEntity.description" label="描述">{{ selectedEntity.description }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedEntity.attributes && selectedEntity.attributes !== '{}'" label="属性">
            <pre style="margin:0;font-size:12px;white-space:pre-wrap">{{ prettyAttr(selectedEntity.attributes) }}</pre>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 规格参数卡（connector 关联的 param） -->
        <div v-if="specParams.length > 0" style="margin-top:16px">
          <h4 style="margin:0 0 8px">规格参数</h4>
          <el-table :data="specParams" size="small">
            <el-table-column prop="name" label="参数" min-width="90" />
            <el-table-column label="值" min-width="100">
              <template #default="{ row }">
                <span style="font-weight:600">{{ specValue(row) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 语义关系（材料→工艺/标准等） -->
        <div v-if="entityRelations.length > 0" style="margin-top:16px">
          <h4 style="margin:0 0 8px">语义关系</h4>
          <div v-for="(r, i) in entityRelations" :key="i" class="rel-item">
            <el-tag size="small" :color="relColor(r.rel_type)" effect="plain" style="border:none">{{ r.rel_type }}</el-tag>
            <span v-if="r.direction === 'out'" class="rel-arrow">→</span>
            <span v-else class="rel-arrow">←</span>
            <span class="rel-target" :class="{ 'rel-muted': r.direction === 'in' }">{{ r.other_name }}</span>
            <span style="color:var(--text-tertiary);font-size:12px">({{ typeLabel(r.other_type) }})</span>
          </div>
        </div>

        <h4 style="margin:16px 0 8px">关联文档</h4>
        <el-table :data="entityFiles" size="small" max-height="260">
          <el-table-column prop="file_name" label="文件" min-width="140" show-overflow-tooltip />
          <el-table-column prop="mention_count" label="提及次数" width="80" align="center" />
        </el-table>

        <h4 style="margin:16px 0 8px">关联片段</h4>
        <div v-for="(c, i) in entityChunks" :key="i" class="chunk-item">
          <div class="chunk-file">{{ c.file_name }}</div>
          <div class="chunk-text">{{ truncate(c.content, 120) }}</div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import * as d3 from 'd3'
import { useRouter } from 'vue-router'
import { Loading } from '@element-plus/icons-vue'
import api from '../api'

const router = useRouter()
const container = ref(null)
const svgEl = ref(null)
const loading = ref(true)
const nodes = ref([])
const edges = ref([])
const legend = ref([])
const edgeLegend = ref([])
const mode = ref('document')

// 全量节点（未筛选，供本地细分筛选）
const allNodes = ref([])

// 实体类型筛选
const typeFilter = ref([])
const activeTypes = ref([])
// 关系类型筛选
const relTypeFilter = ref([])
const activeRelTypes = ref([])

// 反链面板
const drawerVisible = ref(false)
const drawerTitle = ref('')
const selectedEntity = ref(null)
const entityFiles = ref([])
const entityChunks = ref([])
const specParams = ref([])
const entityRelations = ref([])

// 所有边（未筛选，供图例/筛选用）
const allEdges = ref([])

// 大图截断：节点数超过上限时按度取 Top 核心节点（避免 SVG 全量渲染卡死）
const MAX_GRAPH_NODES = 300
const graphTruncated = ref(false)
// 是否始终显示节点标签（默认关闭，hover 显示，Obsidian 风格）
const showLabels = ref(false)

let simulation = null

// 实体类型颜色映射
const TYPE_COLORS = {
  connector: '#1677ff',   // 蓝
  material: '#52c41a',    // 绿
  standard: '#fa8c16',    // 橙（基础色，按 category 细分时会被覆盖）
  process: '#722ed1',     // 紫
  param: '#eb2f96',       // 粉
  unknown: '#8c8c8c',
}
// 标准号按 standard_domain 细分着色（standard 节点的 attributes.standard_domain）
// 领域词表与后端 STANDARD_DOMAINS 严格一致
const STD_CATEGORY_COLORS = {
  '材料': '#f5222d',        // 红
  '紧固件': '#faad14',      // 金黄
  '工艺': '#722ed1',        // 紫
  '机械制图': '#13c2c2',    // 青
  '电工': '#eb2f96',        // 粉
  '轴承': '#a0d911',        // 黄绿
  '密封件': '#2f54eb',      // 深蓝
  '公差配合': '#fa8c16',    // 橙
  '基础标准': '#8c8c8c',    // 灰
  '其他': '#bfbfbf',        // 浅灰
}
const TYPE_LABELS = {
  connector: '连接器',
  material: '材料',
  standard: '标准',
  process: '工艺',
  param: '参数',
  unknown: '未知',
}
const docColors = ['#1677ff', '#52c41a', '#fa8c16', '#eb2f96', '#722ed1', '#13c2c2', '#f5222d', '#faad14']

// 关系类型颜色映射（边类型 → 颜色）
const REL_COLORS = {
  cooccur: '#bfbfbf',          // 灰：无差别共现
  spec: '#13c2c2',             // 青：规格关系
  compatible_process: '#52c41a', // 绿：材料-工艺相容
  uses_standard: '#fa8c16',    // 橙：材料-标准引用
  similar: '#722ed1',          // 紫：文档相似
  keyword: '#1677ff',          // 蓝：关键词
}
const REL_LABELS = {
  cooccur: '共现',
  spec: '规格',
  compatible_process: '材料→工艺',
  uses_standard: '材料→标准',
  similar: '文档相似',
  keyword: '关键词',
}

function relColor(t) { return REL_COLORS[t] || '#bfbfbf' }
function relLabel(t) { return REL_LABELS[t] || t }

function typeColor(t) { return TYPE_COLORS[t] || TYPE_COLORS.unknown }
function typeLabel(t) { return TYPE_LABELS[t] || t }

// 读取 standard 节点的标准号领域（从 attributes JSON 里解 standard_domain）
function nodeCategory(n) {
  if (!n || n.type !== 'standard') return null
  if (!n.attributes) return null
  let a = n.attributes
  if (typeof a === 'string') {
    try { a = JSON.parse(a) } catch { return null }
  }
  return a.standard_domain || null
}

// 节点实际颜色：standard 按 category 细分，其他按 type
function nodeColor(n) {
  if (n.type === 'standard') {
    const c = nodeCategory(n)
    if (c && STD_CATEGORY_COLORS[c]) return STD_CATEGORY_COLORS[c]
  }
  return typeColor(n.type)
}

// 节点的图例显示名：standard 显示「标准·类别」，其他显示 typeLabel
function nodeLabel(n) {
  if (n.type === 'standard') {
    const c = nodeCategory(n)
    return c ? `标准·${c}` : '标准'
  }
  return typeLabel(n.type)
}

onMounted(async () => {
  await fetchGraph()
})

onUnmounted(() => {
  if (simulation) simulation.stop()
})

watch(mode, () => fetchGraph())
watch(activeTypes, () => { applyNodeFilter(); applyEdgeFilter(); buildEntityLegend(); renderGraph() })
watch(activeRelTypes, () => { applyEdgeFilter(); renderGraph() })
watch(showLabels, () => renderGraph())

async function fetchGraph() {
  loading.value = true
  try {
    if (mode.value === 'document') {
      const { data } = await api.get('/graph')
      nodes.value = data.data.nodes || []
      // 文档图边字段映射：source_id/target_id → source/target（d3 forceLink 约定）
      edges.value = (data.data.edges || []).map(e => ({
        ...e,
        source: e.source_id ?? e.source,
        target: e.target_id ?? e.target,
      }))
      buildDocLegend()
    } else {
      // 实体图：全量拉取，筛选在前端本地做（支持 standard 按 category 细分）
      const { data } = await api.get('/entities/graph')
      allNodes.value = data.data.nodes || []
      // 实体图边字段映射：source_id/target_id → source/target（d3 forceLink 约定）
      allEdges.value = (data.data.edges || []).map(e => ({
        ...e,
        source: e.source_id ?? e.source,
        target: e.target_id ?? e.target,
      }))
      // 大图截断：节点过多时按度取 Top 核心，避免 SVG 全量渲染卡顿
      applyGraphTruncation()
      applyNodeFilter()
      applyEdgeFilter()
      buildEntityLegend()
      buildEdgeLegend()
    }
    await nextTick()
    renderGraph()
  } catch (e) {
    console.error('图谱加载失败', e)
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

function buildDocLegend() {
  const cats = {}
  nodes.value.forEach(n => {
    const cat = n.category || '未分类'
    cats[cat] = (cats[cat] || 0) + 1
  })
  legend.value = Object.entries(cats).slice(0, 8).map(([name, count], i) => ({
    name, count, color: docColors[i % docColors.length]
  }))
}

function buildEntityLegend() {
  const cats = {}
  nodes.value.forEach(n => {
    if (n.type === 'standard') {
      const c = nodeCategory(n) || '未分类'
      const key = 'std:' + c
      cats[key] = (cats[key] || 0) + 1
    } else {
      const t = n.type || 'unknown'
      cats[t] = (cats[t] || 0) + 1
    }
  })
  legend.value = Object.entries(cats).map(([name, count]) => {
    if (name.startsWith('std:')) {
      const c = name.slice(4)
      return { name: `标准·${c}`, count, color: STD_CATEGORY_COLORS[c] || TYPE_COLORS.standard, stdCat: c }
    }
    return { name: typeLabel(name), count, color: typeColor(name) }
  })
  // 更新类型筛选器选项
  typeFilter.value = Object.entries(cats).map(([name, count]) => {
    if (name.startsWith('std:')) {
      const c = name.slice(4)
      return { name: 'std:' + c, label: `标准·${c}`, count, stdCat: c }
    }
    return { name, label: typeLabel(name), count }
  })
}

function buildEdgeLegend() {
  const cats = {}
  allEdges.value.forEach(e => {
    const t = e.rel_type || 'cooccur'
    cats[t] = (cats[t] || 0) + 1
  })
  edgeLegend.value = Object.entries(cats).map(([name, count]) => ({
    name, label: relLabel(name), count, color: relColor(name)
  }))
  // 更新关系类型筛选器选项
  relTypeFilter.value = Object.entries(cats).map(([name, count]) => ({
    name, label: relLabel(name), count
  }))
}

function applyEdgeFilter() {
  if (activeRelTypes.value.length === 0) {
    edges.value = allEdges.value
  } else {
    edges.value = allEdges.value.filter(e => activeRelTypes.value.includes(e.rel_type))
  }
  // 边只保留两端节点都在当前 nodes 里的
  const nodeIds = new Set(nodes.value.map(n => n.id))
  edges.value = edges.value.filter(e =>
    nodeIds.has(e.source_id ?? e.source) && nodeIds.has(e.target_id ?? e.target))
}

function applyGraphTruncation() {
  // 大图截断：实体图节点数超上限时，按度取 Top 核心节点，避免 SVG 全量渲染卡死
  graphTruncated.value = false
  if (allNodes.value.length <= MAX_GRAPH_NODES) return
  const sorted = [...allNodes.value].sort((a, b) => (b.degree || 0) - (a.degree || 0))
  const keepIds = new Set(sorted.slice(0, MAX_GRAPH_NODES).map(n => n.id))
  allNodes.value = sorted.slice(0, MAX_GRAPH_NODES)
  // 边只保留两端节点都在保留集内的
  allEdges.value = allEdges.value.filter(e =>
    keepIds.has(e.source_id ?? e.source) && keepIds.has(e.target_id ?? e.target))
  graphTruncated.value = true
}

function applyNodeFilter() {
  if (activeTypes.value.length === 0) {
    nodes.value = allNodes.value
    return
  }
  // 拆分选择：普通 type（连接器/材料/工艺/参数）与 standard 细分（std:xxx）
  const plainTypes = new Set()
  const stdCats = new Set()
  activeTypes.value.forEach(v => {
    if (v.startsWith('std:')) stdCats.add(v.slice(4))
    else plainTypes.add(v)
  })
  nodes.value = allNodes.value.filter(n => {
    if (n.type === 'standard') {
      // standard 类型：若选定了细分类别，按 category 匹配
      if (stdCats.size > 0) {
        return stdCats.has(nodeCategory(n) || '未分类')
      }
      return plainTypes.has('standard')
    }
    return plainTypes.has(n.type)
  })
}

function renderGraph() {
  const el = svgEl.value
  if (!el || nodes.value.length === 0) return

  const width = el.parentElement.clientWidth
  const height = el.parentElement.clientHeight

  d3.select(el).selectAll('*').remove()
  const svg = d3.select(el).attr('width', width).attr('height', height)
  const g = svg.append('g')

  const zoom = d3.zoom().scaleExtent([0.3, 3]).on('zoom', (e) => g.attr('transform', e.transform))
  svg.call(zoom)

  const isEntity = mode.value === 'entity'
  const nData = nodes.value.map(n => ({
    ...n,
    color: isEntity ? nodeColor(n) : docColors[(n.category || '') .length % docColors.length],
    radius: isEntity
      ? Math.max(8, Math.min(24, Math.sqrt((n.degree || 0) * 6) + 6))
      : Math.max(7, Math.min(22, Math.sqrt((n.chunk_count || 1) * 6) + 5))
  }))

  const link = g.append('g').selectAll('line')
    .data(edges.value)
    .join('line')
    .attr('stroke', d => isEntity ? relColor(d.rel_type) : '#c8c8c8')
    .attr('stroke-width', d => Math.max(0.4, (d.weight || 1) * 0.3))
    .attr('stroke-opacity', d => (isEntity && d.rel_type === 'cooccur') ? 0.10 : 0.45)

  const node = g.append('g').selectAll('g')
    .data(nData)
    .join('g')
    .attr('class', 'graph-node')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y })
      .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null })
    )
    .on('click', (e, d) => {
      if (isEntity) openEntityDetail(d.id, d.name)
      else router.push(`/document/${d.id}`)
    })
    .on('mouseover', (e, d) => highlightNeighbors(d))
    .on('mouseout', () => clearHighlight())

  node.append('circle')
    .attr('r', d => d.radius)
    .attr('fill', d => d.color)
    .attr('fill-opacity', 0.8)
    .attr('stroke', d => d3.color(d.color).darker(0.3))
    .attr('stroke-width', 1.5)

  node.append('text')
    .text(d => (d.name || '').length > 12 ? (d.name || '').slice(0, 12) + '...' : (d.name || ''))
    .attr('text-anchor', 'middle')
    .attr('dy', d => d.radius + 14)
    .attr('font-size', 11)
    .attr('fill', 'var(--text-secondary)')
    .attr('opacity', showLabels.value ? 1 : 0)  // 默认隐藏标签，hover 时显示（Obsidian 风格）
    .attr('pointer-events', 'none')

  simulation = d3.forceSimulation(nData)
    .force('link', d3.forceLink(edges.value).id(d => d.id).distance(isEntity ? 70 : 90))
    .force('charge', d3.forceManyBody().strength(isEntity ? -300 : -260))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('x', d3.forceX(width / 2).strength(0.05))
    .force('y', d3.forceY(height / 2).strength(0.05))
    .force('collision', d3.forceCollide().radius(d => d.radius + 6))
    .on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })

  // === hover 高亮邻接（Obsidian 式交互）===
  function highlightNeighbors(d) {
    // 找出直接邻居 id（实体图边已映射 source/target；文档图映射后也是 source/target）
    const neighborIds = new Set([d.id])
    edges.value.forEach(e => {
      const s = e.source_id ?? e.source
      const t = e.target_id ?? e.target
      // source/target 可能是对象（forceLink 已解析）或原始 id
      const sid = typeof s === 'object' ? s.id : s
      const tid = typeof t === 'object' ? t.id : t
      if (sid === d.id) neighborIds.add(tid)
      if (tid === d.id) neighborIds.add(sid)
    })
    // 高亮邻居节点，淡出其余
    node.select('circle')
      .attr('fill-opacity', n => neighborIds.has(n.id) ? 0.9 : 0.12)
    node.select('text')
      .attr('opacity', n => neighborIds.has(n.id) ? 1 : 0)
      .attr('fill-opacity', n => neighborIds.has(n.id) ? 1 : 0.15)
    // 边：连接邻居的高亮，其余淡出
    link
      .attr('stroke-opacity', e => {
        const s = e.source_id ?? e.source
        const t = e.target_id ?? e.target
        const sid = typeof s === 'object' ? s.id : s
        const tid = typeof t === 'object' ? t.id : t
        const isNeighborEdge = neighborIds.has(sid) && neighborIds.has(tid)
        return isNeighborEdge ? 0.6 : 0.03
      })
  }

  function clearHighlight() {
    node.select('circle')
      .attr('fill-opacity', 0.8)
    node.select('text')
      .attr('opacity', showLabels.value ? 1 : 0)
      .attr('fill-opacity', 1)
    link
      .attr('stroke-opacity', e => (isEntity && e.rel_type === 'cooccur') ? 0.10 : 0.45)
  }
}

async function openEntityDetail(id, name) {
  drawerTitle.value = name
  drawerVisible.value = true
  specParams.value = []
  entityRelations.value = []
  selectedEntity.value = null
  try {
    const { data } = await api.get(`/entities/${id}`)
    selectedEntity.value = data.data.entity
    entityFiles.value = data.data.files || []
    entityChunks.value = data.data.chunks || []
    specParams.value = (data.data.spec_params || []).map(p => ({
      ...p,
      attributes: typeof p.attributes === 'string' ? safeParse(p.attributes) : p.attributes,
    }))
    entityRelations.value = data.data.relations || []
  } catch (e) {
    console.error('实体详情加载失败', e)
  }
}

function safeParse(s) {
  try { return JSON.parse(s) } catch { return {} }
}

function specValue(row) {
  const a = row.attributes || {}
  const v = a.value ?? a.数值 ?? ''
  const u = a.unit ?? a.单位 ?? ''
  return v ? `${v} ${u}`.trim() : ''
}

function prettyAttr(attr) {
  if (!attr) return ''
  if (typeof attr === 'string') {
    try { return JSON.stringify(JSON.parse(attr), null, 2) } catch { return attr }
  }
  return JSON.stringify(attr, null, 2)
}

function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '...' : s
}
</script>

<style scoped>
.graph-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.graph-tabs {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color, #eee);
  flex-shrink: 0;
}
.graph-container {
  position: relative;
  flex: 1;
  min-height: 0;
}
.graph-container svg {
  width: 100%;
  height: 100%;
}
.graph-truncated-hint {
  position: absolute;
  left: 50%;
  top: 16px;
  transform: translateX(-50%);
  background: rgba(250, 140, 22, 0.92);
  color: #fff;
  font-size: 12px;
  padding: 6px 14px;
  border-radius: 16px;
  z-index: 11;
  pointer-events: none;
  white-space: nowrap;
}
.loading-center {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-tertiary);
}
.empty-state .el-icon { font-size: 48px; }
.graph-legend {
  position: absolute;
  right: 16px;
  top: 16px;
  background: var(--bg-color, #fff);
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  font-size: 13px;
  z-index: 10;
}
.graph-node { cursor: pointer; }
.chunk-item {
  padding: 8px;
  border: 1px solid #eee;
  border-radius: 6px;
  margin-bottom: 8px;
}
.chunk-file {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.chunk-text {
  font-size: 13px;
  line-height: 1.5;
}
.rel-item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 13px;
}
.rel-arrow {
  color: var(--text-tertiary);
  font-weight: 600;
}
.rel-target {
  font-weight: 600;
  color: var(--text-primary);
}
.rel-muted {
  color: var(--text-secondary);
  font-weight: 500;
}
</style>
