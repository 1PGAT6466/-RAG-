<template>
  <div class="graph-page">
    <PageBack to="/" label="返回对话" />
    <!-- 顶部 Tab 切换：双图谱 -->
    <div class="graph-tabs">
      <el-radio-group v-model="mode" size="large">
        <el-radio-button value="document">文档引用图</el-radio-button>
        <el-radio-button value="entity">实体关系图</el-radio-button>
        <el-radio-button value="timeline">时间轴</el-radio-button>
      </el-radio-group>

      <div class="entity-filter" v-if="mode === 'entity'">
        <el-select v-model="activeTypes" multiple collapse-tags placeholder="按实体类型/标准类别筛选" style="width: 320px">
          <el-option v-for="t in typeFilter" :key="t.name" :label="`${t.label} (${t.count})`" :value="t.name" />
        </el-select>
        <el-select v-model="activeRelTypes" multiple collapse-tags placeholder="按关系类型筛选" style="width: 280px">
          <el-option v-for="r in relTypeFilter" :key="r.name" :label="`${r.label} (${r.count})`" :value="r.name" />
        </el-select>
      </div>

      <el-checkbox v-if="mode === 'entity'" v-model="hideCooccur" style="margin-left:auto">隐藏共现噪声边</el-checkbox>
      <el-checkbox v-model="showLabels" style="margin-left:auto">显示标签</el-checkbox>

      <!-- 局部图谱模式：返回全图 -->
      <el-button v-if="localCenter !== null" size="small" @click="backToFullGraph" style="margin-left:8px">
        <el-icon><Back /></el-icon>&nbsp;返回全图
      </el-button>
    </div>

    <!-- 图谱画布 -->
    <div class="graph-container" ref="container">
      <!-- 时间轴视图（按入库时间展示实体演进） -->
      <div v-if="mode === 'timeline'" class="timeline-view">
        <div v-if="timelineLoading" class="loading-center">
          <LoadingBlock />
        </div>
        <EmptyState v-else-if="timelineGroups.length === 0" icon="Connection" title="暂无实体时间数据" class="graph-empty-abs" />
        <div v-else class="timeline-body">
          <div v-for="g in timelineGroups" :key="g.key" class="timeline-group">
            <div class="timeline-group-header">
              <span class="timeline-dot"></span>
              <span class="timeline-time">{{ g.label }}</span>
              <span class="timeline-count">{{ g.entities.length }} 个实体</span>
            </div>
            <div class="timeline-chips">
              <el-tag
                v-for="e in g.entities"
                :key="e.id"
                size="small"
                :color="nodeColor(e)"
                effect="plain"
                style="border:none;cursor:pointer;color:var(--text-primary)"
                @click="openEntityDetail(e.id, e.name)"
              >
                {{ e.name }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>

      <canvas v-if="mode !== 'timeline'" ref="canvasEl" role="img" aria-label="知识图谱可视化"></canvas>
      <div v-if="graphTruncated" class="graph-truncated-hint">
        节点过多，已按关联度显示 Top {{ MAX_GRAPH_NODES }} 核心节点，可用上方筛选缩小范围
      </div>
      <div v-if="loading" class="loading-center">
        <LoadingBlock />
      </div>
      <EmptyState
        v-if="!loading && nodes.length === 0"
        icon="Connection"
        :title="mode === 'document' ? '暂无文档图谱' : '暂无实体图谱'"
        :hint="mode === 'document' ? '上传文档后自动建立关联' : '入库后自动抽取实体与关系'"
        class="graph-empty-abs"
      />
      <!-- 图例：默认折叠成小按钮，展开时限制高度可滚动，不再遮挡画布 -->
      <div v-if="nodes.length > 0" class="graph-legend" :class="{ 'legend-collapsed': legendCollapsed }">
        <div class="legend-toggle" @click="legendCollapsed = !legendCollapsed">
          <span>{{ legendCollapsed ? '图例' : '收起图例' }}</span>
          <span class="legend-arrow">{{ legendCollapsed ? '▲' : '▼' }}</span>
        </div>
        <div v-show="!legendCollapsed" class="legend-body">
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
    </div>

    <!-- 反链面板（点击实体/文档后展示） -->
    <el-drawer v-model="drawerVisible" :title="drawerTitle" size="380px">
      <div v-if="selectedEntity" class="entity-detail">
        <!-- 局部图谱展开：以当前实体为中心，只看 N 跳邻域 -->
        <div style="margin-bottom:12px;display:flex;gap:8px">
          <el-button size="small" type="primary" plain @click="expandLocalGraph">
            <el-icon><Share /></el-icon>&nbsp;展开局部图谱
          </el-button>
          <el-select v-model="localHops" size="small" style="width:90px" placeholder="跳数">
            <el-option label="1 跳" :value="1" />
            <el-option label="2 跳" :value="2" />
            <el-option label="3 跳" :value="3" />
          </el-select>
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="类型">{{ typeLabel(selectedEntity.type) }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedEntity.degree !== undefined" label="连接度">
            <span>总 {{ selectedEntity.degree }}</span>
            <span v-if="selectedEntity.in_degree !== undefined" style="margin-left:12px;color:var(--text-secondary)">入 {{ selectedEntity.in_degree }} / 出 {{ selectedEntity.out_degree }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="selectedEntity.created_at" label="入库时间">{{ selectedEntity.created_at }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedEntity.description" label="描述">
            <span style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">{{ selectedEntity.description }}</span>
          </el-descriptions-item>
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
import { Share, Back } from '@element-plus/icons-vue'
import PageBack from '../components/PageBack.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import { friendlyError } from '../utils/errors'
import api from '../api'
import {
  TYPE_COLORS, STD_CATEGORY_COLORS, TYPE_LABELS, DOC_COLORS,
  REL_COLORS, REL_LABELS,
  relColor, relLabel, typeColor, typeLabel, nodeCategory, nodeColor, nodeLabel,
} from './graph/constants'
import { safeParse, specValue, prettyAttr, truncate } from './graph/helpers'

const router = useRouter()
const container = ref(null)
const canvasEl = ref(null)
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
// 图例折叠状态（默认收起，避免大图例面板遮挡画布）
const legendCollapsed = ref(true)
const drawerTitle = ref('')
const selectedEntity = ref(null)
const entityFiles = ref([])
const entityChunks = ref([])
const specParams = ref([])
const entityRelations = ref([])
// 局部图谱：展开跳数 + 当前中心实体
const localHops = ref(2)
const localCenter = ref(null)
// 时间轴视图
const timelineGroups = ref([])
const timelineLoading = ref(false)
// 局部图谱切 mode 时的 fetch 抑制（避免 watch(mode) 拉全图覆盖局部数据）
const suppressModeFetch = ref(false)

// 所有边（未筛选，供图例/筛选用）
const allEdges = ref([])

// 大图截断：节点数超过上限时按度取 Top 核心节点（避免 SVG 全量渲染卡死）
const MAX_GRAPH_NODES = 300
const graphTruncated = ref(false)
// 是否始终显示节点标签（默认关闭，hover 显示，Obsidian 风格）
const showLabels = ref(false)
// 实体图默认隐藏 cooccur 噪声边（4225 条共现边会形成灰色雾团遮挡结构）
const hideCooccur = ref(true)

let simulation = null

// 每次 renderGraph 重新绑定的全局监听器清理函数（避免切 Tab / 重新渲染时重复绑定 window 监听）
let graphCleanup = null

onMounted(async () => {
  await fetchGraph()
})

onUnmounted(() => {
  if (graphCleanup) { graphCleanup(); graphCleanup = null }
  if (simulation) simulation.stop()
})

watch(mode, () => {
  if (suppressModeFetch.value) { suppressModeFetch.value = false; return }
  // 切出 entity 或局部图时，清理局部中心标记
  if (mode.value !== 'entity') localCenter.value = null
  fetchGraph()
})
watch(activeTypes, () => { applyNodeFilter(); applyEdgeFilter(); buildEntityLegend(); renderGraph() })
watch(activeRelTypes, () => { applyEdgeFilter(); renderGraph() })
watch(hideCooccur, () => { applyEdgeFilter(); renderGraph() })
watch(showLabels, () => renderGraph())

let _graphAbortController = null

async function fetchGraph() {
  // 取消上一次未完成的请求
  if (_graphAbortController) _graphAbortController.abort()
  _graphAbortController = new AbortController()
  const signal = _graphAbortController.signal

  // 时间轴视图：加载实体并按入库时间分组，不走画布渲染
  if (mode.value === 'timeline') {
    loading.value = false
    await fetchTimeline()
    return
  }
  loading.value = true
  try {
    if (mode.value === 'document') {
      const { data } = await api.get('/graph', { signal })
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
      const { data } = await api.get('/entities/graph', { signal })
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
    if (e.name === 'AbortError') return
    console.error('图谱加载失败', e)
    ElMessage.error('图谱加载失败：' + friendlyError(e))
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

async function fetchTimeline() {
  timelineLoading.value = true
  try {
    const { data } = await api.get('/entities/graph')
    const ns = data.data.nodes || []
    // 按 created_at 分组（取到分钟，保留时间演进粒度）
    // 时间跨度较短时按「小时:分钟」分组，跨多天时按「天」分组
    const groups = {}
    ns.forEach(n => {
      if (!n.created_at) return
      const dt = n.created_at.replace('T', ' ')
      // 取日期部分 + 小时作为分组键：适合「同一天批量入库」也能看出先后
      const datePart = dt.slice(0, 10)   // YYYY-MM-DD
      const hourPart = dt.slice(11, 16)  // HH:MM
      const key = datePart + ' ' + hourPart
      if (!groups[key]) groups[key] = []
      groups[key].push(n)
    })
    timelineGroups.value = Object.keys(groups)
      .sort()
      .map(key => ({
        key,
        label: key,
        entities: groups[key].sort((a, b) => (b.degree || 0) - (a.degree || 0)),
      }))
    nodes.value = ns
    edges.value = []
  } catch (e) {
    console.error('时间轴加载失败', e)
    ElMessage.error('时间轴加载失败：' + friendlyError(e))
    timelineGroups.value = []
  } finally {
    timelineLoading.value = false
  }
}

function buildDocLegend() {
  const cats = {}
  nodes.value.forEach(n => {
    const cat = n.category || '未分类'
    cats[cat] = (cats[cat] || 0) + 1
  })
  legend.value = Object.entries(cats).slice(0, 8).map(([name, count], i) => ({
    name, count, color: DOC_COLORS[i % DOC_COLORS.length]
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
  let base = allEdges.value
  // 实体图：隐藏 cooccur 噪声边时先过滤掉，让语义边（spec/compatible_process 等）成为主体
  if (mode.value === 'entity' && hideCooccur.value) {
    base = base.filter(e => e.rel_type !== 'cooccur')
  }
  if (activeRelTypes.value.length === 0) {
    edges.value = base
  } else {
    edges.value = base.filter(e => activeRelTypes.value.includes(e.rel_type))
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

/**
 * renderGraph — Canvas 力导向图渲染器
 *
 * 核心流程：
 * 1. 将 nodes/edges 映射为 d3 forceSimulation 数据
 * 2. 绑定 Canvas 交互事件（拖拽节点 / 平移画布 / 缩放 / hover 高亮）
 * 3. 用 requestAnimationFrame 节流重绘，避免超过 60fps
 *
 * 状态机：dragMode = null | 'node' | 'pan'
 *   - mousedown 检测 hit-test → 决定进入 node 拖拽还是 pan
 *   - mousemove 根据 dragMode 更新 fx/fy 或 transform
 *   - mouseup 判断 moved → 若未移动则触发点击跳转
 */
function renderGraph() {
  const canvas = canvasEl.value
  if (!canvas || nodes.value.length === 0) {
    if (graphCleanup) { graphCleanup(); graphCleanup = null }
    return
  }

  // 清理上一次渲染绑定的全局监听器（避免重复绑定导致拖拽/点击叠加）
  if (graphCleanup) { graphCleanup(); graphCleanup = null }
  if (simulation) simulation.stop()

  const width = canvas.parentElement.clientWidth
  const height = canvas.parentElement.clientHeight
  const dpr = window.devicePixelRatio || 1
  canvas.width = width * dpr
  canvas.height = height * dpr
  canvas.style.width = width + 'px'
  canvas.style.height = height + 'px'
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  const isEntity = mode.value === 'entity'
  const isLocal = isEntity && localCenter.value != null
  const nData = nodes.value.map(n => {
    const deg = n.degree || 0
    // Obsidian 式：节点小巧统一，hub 节点（如 M8=187）也只比普通节点略大，不夸张放大
    const r = isEntity
      ? Math.max(4, Math.min(11, 3.5 + Math.log2(deg + 1) * 0.9))
      : Math.max(6, Math.min(14, Math.log2((n.chunk_count || 1) + 1) * 1.6 + 4))
    const isCenter = isLocal && n.id === localCenter.value
    return {
      ...n,
      color: isEntity ? nodeColor(n) : DOC_COLORS[(n.category || '').length % DOC_COLORS.length],
      radius: isCenter ? r + 3 : r,
      isCenter,
    }
  })

  // 边数据：统一提取 source/target 的 id（d3 forceLink 会解析成对象）
  // 只保留两端都存在于当前 nodes 的边——孤儿边会让 d3 forceLink 抛 `node not found` 导致整图空白
  const nodeIds = new Set(nData.map(n => n.id))
  const edgeList = edges.value
    .map(e => ({
      source: e.source_id ?? e.source,
      target: e.target_id ?? e.target,
      rel_type: e.rel_type,
      weight: e.weight || 1,
    }))
    .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))

  // === 交互状态（Canvas 无 DOM，手动维护状态机） ===
  let transform = d3.zoomIdentity
  let hoveredId = null
  let labelOpacity = showLabels.value ? 1 : 0

  // 拖拽状态
  let dragMode = null // null | 'node' | 'pan'
  let dragNode = null
  let startX = 0, startY = 0
  let moved = false
  let panStart = null

  // 力模拟：forceX/forceY 逐节点居中（而非 forceCenter，避免中心聚团）
  simulation = d3.forceSimulation(nData)
    .force('link', d3.forceLink(edgeList).id(d => d.id).distance(isEntity ? 120 : 100).strength(0.4))
    .force('charge', d3.forceManyBody().strength(isEntity ? -380 : -280))
    .force('x', d3.forceX(width / 2).strength(d => (isLocal && d.isCenter) ? 0.5 : 0.03))
    .force('y', d3.forceY(height / 2).strength(d => (isLocal && d.isCenter) ? 0.5 : 0.03))
    .force('collision', d3.forceCollide().radius(d => d.radius + 10).iterations(2))
    .alphaDecay(0.028) // 加快收敛（默认 0.0228，适当提升让布局更快稳定，停止无用重绘）

  // rAF 节流：合并同一帧内的多次 tick，避免超过 60fps 的多余重绘（大图卡顿主因之一）
  let rafPending = false
  function scheduleDraw() {
    if (rafPending) return
    rafPending = true
    requestAnimationFrame(() => {
      rafPending = false
      draw()
    })
  }
  simulation.on('tick', () => {
    if (simulation.alpha() < simulation.alphaMin()) {
      // 布局已收敛，停止模拟，避免持续空转重绘
      simulation.stop()
      draw()
      return
    }
    scheduleDraw()
  })

  // === 坐标转换 ===
  function localXY(event) {
    const rect = canvas.getBoundingClientRect()
    return [event.clientX - rect.left, event.clientY - rect.top]
  }
  function toWorld(px, py) {
    return [(px - transform.x) / transform.k, (py - transform.y) / transform.k]
  }

  // === hit-test：从上层往下找节点 ===
  function findNode(px, py) {
    const [wx, wy] = toWorld(px, py)
    for (let i = nData.length - 1; i >= 0; i--) {
      const n = nData[i]
      if (n.x == null || n.y == null) continue
      const dx = n.x - wx, dy = n.y - wy
      const hit = n.radius + 4
      if (dx * dx + dy * dy <= hit * hit) return n
    }
    return null
  }

  // === 事件监听（一次性绑定，用状态机区分拖拽/平移/点击） ===
  canvas.addEventListener('mousedown', onMouseDown)
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('mouseleave', onLeaveGraph)

  // 注册清理函数：下次渲染或组件卸载时移除这些监听
  graphCleanup = () => {
    canvas.removeEventListener('mousedown', onMouseDown)
    window.removeEventListener('mousemove', onMouseMove)
    window.removeEventListener('mouseup', onMouseUp)
    canvas.removeEventListener('wheel', onWheel)
    canvas.removeEventListener('mouseleave', onLeaveGraph)
    if (simulation) simulation.stop()
  }

  function onLeaveGraph() { hoveredId = null; draw() }

  function onMouseDown(event) {
    const [px, py] = localXY(event)
    const n = findNode(px, py)
    startX = px; startY = py
    moved = false
    if (n) {
      dragMode = 'node'
      dragNode = n
      if (!simulation) return
      simulation.alphaTarget(0.3).restart()
      n.fx = n.x; n.fy = n.y
    } else {
      dragMode = 'pan'
      panStart = { x: transform.x, y: transform.y, mx: px, my: py }
    }
    event.preventDefault()
  }

  function onMouseMove(event) {
    const [px, py] = localXY(event)
    if (dragMode === 'node' && dragNode) {
      const [wx, wy] = toWorld(px, py)
      const dx = px - startX, dy = py - startY
      if (dx * dx + dy * dy > 9) moved = true
      dragNode.fx = wx; dragNode.fy = wy
      draw()
      return
    }
    if (dragMode === 'pan' && panStart) {
      const dx = px - panStart.mx, dy = py - panStart.my
      if (dx * dx + dy * dy > 9) moved = true
      transform = d3.zoomIdentity.translate(panStart.x + dx, panStart.y + dy).scale(transform.k)
      draw()
      return
    }
    // 纯 hover
    const n = findNode(px, py)
    const newHover = n ? n.id : null
    if (newHover !== hoveredId) {
      hoveredId = newHover
      canvas.style.cursor = n ? 'pointer' : 'grab'
      draw()
    }
  }

  function onMouseUp(event) {
    if (dragMode === 'node' && dragNode) {
      dragNode.fx = null; dragNode.fy = null
      simulation.alphaTarget(0)
      // 未发生移动 → 视为点击，触发跳转
      if (!moved) {
        const d = dragNode
        dragMode = null; dragNode = null; moved = false
        if (isEntity) openEntityDetail(d.id, d.name)
        else router.push(`/document/${d.id}`)
        return
      }
    }
    dragMode = null; dragNode = null; moved = false; panStart = null
    canvas.style.cursor = 'grab'
  }

  function onWheel(event) {
    event.preventDefault()
    const [px, py] = localXY(event)
    const [wx, wy] = toWorld(px, py)
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15
    const nk = Math.max(0.1, Math.min(8, transform.k * factor))
    const kk = nk / transform.k
    transform = d3.zoomIdentity
      .translate(px - wx * nk, py - wy * nk)
      .scale(nk)
    if (!showLabels.value) {
      labelOpacity = Math.max(0, Math.min(1, (nk - 1.2) / 0.6))
    }
    draw()
  }

  function neighborSet(d) {
    const set = new Set([d.id])
    edgeList.forEach(e => {
      const sid = typeof e.source === 'object' ? e.source.id : e.source
      const tid = typeof e.target === 'object' ? e.target.id : e.target
      if (sid === d.id) set.add(tid)
      if (tid === d.id) set.add(sid)
    })
    return set
  }

  // hover 邻居集合缓存：neighborSet 遍历全部边是 O(E)，只在 hoveredId 变化时重算一次
  let cachedHoverId = null
  let cachedNeighbors = null
  let cachedHoverNode = null

  function getHoverContext() {
    if (hoveredId === cachedHoverId) {
      return { hoveredNode: cachedHoverNode, neighbors: cachedNeighbors }
    }
    cachedHoverId = hoveredId
    cachedHoverNode = hoveredId != null ? nData.find(n => n.id === hoveredId) || null : null
    cachedNeighbors = cachedHoverNode ? neighborSet(cachedHoverNode) : null
    return { hoveredNode: cachedHoverNode, neighbors: cachedNeighbors }
  }

  function draw() {
    ctx.clearRect(0, 0, width, height)
    ctx.save()
    ctx.translate(transform.x, transform.y)
    ctx.scale(transform.k, transform.k)

    // 计算 hover 邻居集合（缓存，仅 hoveredId 变化时重算）
    const { hoveredNode, neighbors } = getHoverContext()

    // 先画边
    edgeList.forEach(e => {
      const s = e.source, t = e.target
      if (!s || !t || s.x == null || t.x == null) return
      const sid = typeof s === 'object' ? s.id : s
      const tid = typeof t === 'object' ? t.id : t
      // cooccur 边按 weight 归一化透明度：weight=1 噪声极淡，高权重共现更实
      // （对数缩放：weight=1→0.04，weight=10→0.13，weight=60→0.26）；语义边固定 0.20
      let opacity = 0.20
      if (isEntity && e.rel_type === 'cooccur') {
        const w = e.weight || 1
        opacity = Math.min(0.30, 0.04 + Math.log2(w + 1) * 0.035)
      }
      let width = Math.max(0.3, e.weight * 0.18)
      if (neighbors) {
        const isNeighbor = neighbors.has(sid) && neighbors.has(tid)
        opacity = isNeighbor ? 0.5 : 0.02
        width = isNeighbor ? 0.8 : 0.3
      }
      ctx.strokeStyle = isEntity ? relColor(e.rel_type) : '#a8adbb'
      ctx.globalAlpha = opacity
      ctx.lineWidth = width
      ctx.beginPath()
      ctx.moveTo(s.x, s.y)
      ctx.lineTo(t.x, t.y)
      ctx.stroke()
    })

    // 再画节点
    nData.forEach(n => {
      if (n.x == null || n.y == null) return
      let alpha = 0.85
      let isHover = false
      if (neighbors) {
        isHover = neighbors.has(n.id)
        alpha = isHover ? 0.95 : 0.06
      }
      ctx.globalAlpha = alpha
      ctx.fillStyle = n.color
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2)
      ctx.fill()

      // 局部图谱中心节点：铜橙描边强调 + 轻微光晕
      if (isLocal && n.isCenter) {
        ctx.globalAlpha = alpha
        ctx.strokeStyle = '#c2703d'
        ctx.lineWidth = 3
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2)
        ctx.globalAlpha = alpha * 0.25
        ctx.strokeStyle = '#c2703d'
        ctx.lineWidth = 1.5
        ctx.stroke()
      }

      // 标签：缩放渐显或 hover 显示；中心节点始终显示标签
      const showText = n.isCenter || isHover || labelOpacity > 0.01
      if (showText) {
        const name = (n.name || '').length > 10 ? (n.name || '').slice(0, 10) + '…' : (n.name || '')
        ctx.globalAlpha = isHover || n.isCenter ? 1 : labelOpacity
        ctx.fillStyle = n.isCenter ? '#c2703d' : '#8a8f98'
        ctx.font = n.isCenter ? 'bold 11px sans-serif' : '10px sans-serif'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        ctx.fillText(name, n.x, n.y + n.radius + 6)
      }
    })

    ctx.restore()
    ctx.globalAlpha = 1
  }

  // 初始绘制
  draw()
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

async function expandLocalGraph() {
  if (!selectedEntity.value) return
  const id = selectedEntity.value.id
  const hops = localHops.value
  try {
    const { data } = await api.get(`/entities/${id}/graph`, { params: { hops } })
    const g = data.data
    // 切到实体图模式，把局部邻域数据直接作为当前节点/边集
    // 用 suppressModeFetch 抑制 watch(mode) 触发 fetchGraph 拉全图覆盖局部数据
    suppressModeFetch.value = true
    mode.value = 'entity'
    localCenter.value = id
    allNodes.value = g.nodes || []
    allEdges.value = (g.edges || []).map(e => ({
      ...e,
      source: e.source_id ?? e.source,
      target: e.target_id ?? e.target,
    }))
    // 局部邻域里 cooccur 仍是主要噪声，默认隐藏以突出语义关系
    hideCooccur.value = true
    activeTypes.value = []
    activeRelTypes.value = []
    graphTruncated.value = false
    applyNodeFilter()
    applyEdgeFilter()
    buildEntityLegend()
    buildEdgeLegend()
    drawerVisible.value = false
    await nextTick()
    renderGraph()
    const truncMsg = g.truncated ? `（已截断到 ${g.nodes.length} 核心节点，完整邻域 ${g.total_nodes} 节点）` : ''
    ElMessage.success(`已展开「${selectedEntity.value.name}」的 ${hops} 跳局部图谱（${g.nodes.length} 节点 / ${g.edges.length} 边）${truncMsg}`)
  } catch (e) {
    console.error('局部图谱加载失败', e)
    ElMessage.error('局部图谱加载失败：' + friendlyError(e))
  }
}

async function backToFullGraph() {
  localCenter.value = null
  graphTruncated.value = false
  await fetchGraph()
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
  gap: 12px;
  padding: 14px 20px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.graph-container {
  position: relative;
  flex: 1;
  min-height: 0;
}
.graph-container canvas {
  width: 100%;
  height: 100%;
  display: block;
}
.graph-truncated-hint {
  position: absolute;
  left: 50%;
  top: 16px;
  transform: translateX(-50%);
  background: var(--accent-warm);
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
/* EmptyState 组件在 graph-container 内绝对居中定位 */
.graph-empty-abs {
  position: absolute;
  inset: 0;
}
.graph-legend {
  position: absolute;
  right: 16px;
  top: 16px;
  background: var(--bg-elevated, #fff);
  border: 1px solid var(--border, #e2e7eb);
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
  font-size: 13px;
  z-index: 10;
  max-width: 220px;
}
.legend-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-weight: 600;
  user-select: none;
  border-radius: var(--radius);
}
.legend-toggle:hover {
  background: var(--bg-hover);
}
.legend-arrow {
  font-size: 10px;
  color: var(--text-tertiary);
}
.legend-body {
  padding: 0 12px 12px;
  max-height: 46vh;
  overflow-y: auto;
}
.graph-node { cursor: pointer; }
.chunk-item {
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
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

/* === 时间轴视图 === */
.timeline-view {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.timeline-body {
  max-width: 880px;
  margin: 0 auto;
}
.timeline-group {
  position: relative;
  padding: 8px 0 20px 28px;
  border-left: 2px solid var(--border);
  margin-left: 8px;
}
.timeline-group:first-child {
  padding-top: 0;
}
.timeline-group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.timeline-dot {
  position: absolute;
  left: -7px;
  top: 12px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--accent);
  border: 2px solid #fff;
  box-shadow: 0 0 0 2px var(--border);
}
.timeline-group:first-child .timeline-dot {
  top: 4px;
}
.timeline-time {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}
.timeline-count {
  font-size: 12px;
  color: var(--text-tertiary);
}
.timeline-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
/* drawer 实体名截断 */
:deep(.el-drawer__title) {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
