<template>
  <div style="height:100%;overflow-y:auto;padding:24px">
    <div v-if="loadError" class="empty-state">
      <el-icon style="font-size:48px"><CircleClose /></el-icon>
      <p>{{ loadError }}</p>
      <el-button size="small" type="primary" @click="loadFile">重试</el-button>
    </div>
    <div v-else-if="!file" class="loading-center"><LoadingBlock /></div>
    <template v-else>
      <div class="doc-head">
        <div class="doc-head-icon"><span class="doc-head-ext">{{ extLabel(file.ext) }}</span></div>
        <div class="doc-head-info">
          <h2 class="doc-head-title">{{ file.name }}</h2>
          <div class="doc-head-meta">
            {{ file.chunk_count }} 个分块 · {{ formatSize(file.size) }} · {{ file.category || '未分类' }}
            <el-tag v-if="dmsRecord" type="info" size="small" effect="plain" class="doc-head-dms"
              title="该文档来自 SeedDMS，点击打开 DMS 文档源" @click="$router.push('/dms')">
              来源 SeedDMS · v{{ dmsRecord.dms_version }}
            </el-tag>
          </div>
        </div>
        <!-- 文档管理操作（仅管理员可见）：删除 + 改分类/标签 -->
        <div class="doc-head-actions">
          <el-button size="small" :icon="Document" @click="openWithWps">用 WPS 打开</el-button>
          <el-button size="small" :icon="Download" @click="exportMarkdown">导出 Markdown</el-button>
          <el-dropdown v-if="auth.isAdmin" trigger="click" @command="onAction">
            <el-button size="small">管理</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="edit-category">修改分类</el-dropdown-item>
                <el-dropdown-item command="edit-tags">修改标签</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除文档</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <!-- 可读模式切换 -->
      <div v-if="chunks.length > 0" style="margin-bottom:12px;display:flex;align-items:center;gap:10px">
        <el-radio-group v-model="readMode" size="small">
          <el-radio-button value="section">章节视图</el-radio-button>
          <el-radio-button value="read">可读模式（连续文档）</el-radio-button>
        </el-radio-group>
      </div>
      <div v-if="chunks.length === 0" class="empty-state"><p>暂无内容</p></div>

      <!-- 关联文件（Obsidian 式反向链接/引用） -->
      <div v-if="hasBacklinks" class="backlinks-panel" style="background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;margin-bottom:12px">
        <div style="font-weight:600;margin-bottom:12px;font-size:14px">关联文件</div>
        <template v-if="backlinks.incoming && backlinks.incoming.length">
          <div style="font-size:12px;color:var(--text-tertiary);margin-bottom:6px">被以下文件引用（{{ backlinks.incoming.length }}）</div>
          <div v-for="b in backlinks.incoming" :key="'in'+b.source_id"
            class="backlink-item" @click="$router.push(`/document/${b.source_id}`)"
            style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-radius:8px;cursor:pointer;margin-bottom:4px"
            @mouseenter="e => e.currentTarget.style.background='var(--bg-hover)'"
            @mouseleave="e => e.currentTarget.style.background='transparent'">
            <div style="display:flex;align-items:center;gap:8px;min-width:0">
              <el-icon style="color:var(--text-tertiary)"><Connection /></el-icon>
              <span style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ b.source_name }}</span>
            </div>
            <span style="font-size:12px;color:var(--text-tertiary);flex-shrink:0">{{ relLabel(b.link_type) }} · {{ (b.weight * 100).toFixed(0) }}%</span>
          </div>
        </template>
        <template v-if="backlinks.outgoing && backlinks.outgoing.length">
          <div style="font-size:12px;color:var(--text-tertiary);margin:10px 0 6px">引用以下文件（{{ backlinks.outgoing.length }}）</div>
          <div v-for="b in backlinks.outgoing" :key="'out'+b.target_id"
            class="backlink-item" @click="$router.push(`/document/${b.target_id}`)"
            style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-radius:8px;cursor:pointer;margin-bottom:4px"
            @mouseenter="e => e.currentTarget.style.background='var(--bg-hover)'"
            @mouseleave="e => e.currentTarget.style.background='transparent'">
            <div style="display:flex;align-items:center;gap:8px;min-width:0">
              <el-icon style="color:var(--text-tertiary)"><Connection /></el-icon>
              <span style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ b.target_name }}</span>
            </div>
            <span style="font-size:12px;color:var(--text-tertiary);flex-shrink:0">{{ relLabel(b.link_type) }} · {{ (b.weight * 100).toFixed(0) }}%</span>
          </div>
        </template>
        <div v-if="!backlinks.incoming?.length && !backlinks.outgoing?.length" style="font-size:13px;color:var(--text-tertiary);padding:8px 0">
          暂无关联文件（上传更多文档后，系统会自动建立相似度关联）
        </div>
      </div>

      <!-- 文档正文：按章节分组 + Markdown 渲染（可阅读模式） -->
      <div v-if="chunks.length === 0" class="empty-state"><p>暂无内容</p></div>
      <div v-else-if="readMode === 'read'" class="doc-body">
        <!-- 可读模式：完整连续 Markdown 文档流 -->
        <div v-if="fullMarkdownLoading" class="loading-center"><LoadingBlock size="24px" /></div>
        <template v-else>
          <div class="chunk-block full-md">
            <div class="chunk-markdown" v-html="renderMarkdown(fullMarkdown)"></div>
          </div>
          <!-- 图片（Obsidian 式 ![[图]] 展示） -->
          <div v-if="images.length" class="images-panel">
            <div class="images-panel-title">📷 文档图片（{{ images.length }} 张）</div>
            <div class="images-grid">
              <div v-for="img in images" :key="img.id" class="image-card">
                <img :src="imageUrl(img)" :alt="img.filename" loading="lazy" @click="previewImage(img)" />
                <div class="image-meta">
                  <span v-if="img.page !== 0">第 {{ img.page + 1 }} 页</span>
                  <span v-else>附图</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
      <div v-else class="doc-body">
        <div v-for="(sec, si) in sectionedChunks" :key="'sec'+si" class="doc-section">
          <div v-if="sec.heading" class="doc-section-title" :id="'sec-'+si">
            <span class="doc-section-icon">▸</span>{{ sec.heading }}
            <span class="doc-section-count">{{ sec.chunks.length }} 块</span>
          </div>
          <div v-for="c in sec.chunks" :key="c.id || c.chunk_index"
            :ref="el => setChunkRef(c.chunk_index, el)"
            :class="['chunk-block', { 'chunk-block--highlight': c.chunk_index === highlightChunk }]">
            <!-- 被引用角标（第 2 项：反向联动） -->
            <div v-if="chunkRefCounts[c.id]" class="chunk-ref-badge" @click.stop="toggleChunkRefs(c.id)">
              <el-icon><ChatDotRound /></el-icon>
              <span>被引用 {{ chunkRefCounts[c.id] }} 次</span>
              <el-icon class="chunk-ref-arrow"><ArrowDown v-if="expandedChunkRefs !== c.id" /><ArrowUp v-else /></el-icon>
            </div>
            <!-- 展开的引用来源列表 -->
            <div v-if="expandedChunkRefs === c.id" class="chunk-ref-panel">
              <div v-if="refsLoading[c.id]" class="chunk-ref-loading">加载中…</div>
              <div v-else-if="(chunkRefsDetail[c.id] || []).length === 0" class="chunk-ref-empty">暂无引用记录</div>
              <div v-else v-for="r in (chunkRefsDetail[c.id] || [])" :key="'r'+r.message_id+r.ref"
                class="chunk-ref-item" @click.stop="goToConversation(r.conversation_id)">
                <span class="chunk-ref-query" :title="r.query">「{{ r.query || r.conv_title || '对话' }}」</span>
                <span class="chunk-ref-meta">{{ r.created_at ? r.created_at.slice(0, 16).replace('T', ' ') : '' }}</span>
              </div>
            </div>
            <div class="chunk-markdown" v-html="renderMarkdown(isMarkdownSource ? c.content : cleanContent(c.content))"></div>
          </div>
        </div>
      </div>
    </template>

    <!-- 图片预览弹窗 -->
    <el-dialog v-model="imagePreviewVisible" title="图片预览" width="70%" append-to-body>
      <div style="display:flex;justify-content:center;max-height:70vh;overflow:auto">
        <img :src="imagePreviewSrc" style="max-width:100%;height:auto;border-radius:8px" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loading, CircleClose, Connection, Document, Download, ChatDotRound, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import LoadingBlock from '../components/LoadingBlock.vue'
// ElMessage/ElMessageBox 由 unplugin-auto-import 自动引入（含样式）
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import api from '../api'
import dmsApi from '../api/dms'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const file = ref(null)
const chunks = ref([])
const backlinks = ref({ incoming: [], outgoing: [] })
const dmsRecord = ref(null)
const highlightChunk = ref(null)
const loadError = ref('')
const chunkEls = {}
// 第 2 项：chunk 被引用（正向：对话引用→文档；反向：chunk→被哪些对话引用）
const chunkRefCounts = ref({})         // {chunk_id: count}
const chunkRefsDetail = ref({})        // {chunk_id: [refs]}
const refsLoading = ref({})            // {chunk_id: bool} 展开加载中
const expandedChunkRefs = ref(null)    // 当前展开的 chunk_id（同一时间只展一个）
// 可读模式
const readMode = ref('section')
const fullMarkdown = ref('')
const fullMarkdownLoading = ref(false)
const images = ref([])
const imagePreviewVisible = ref(false)
const imagePreviewSrc = ref('')

function previewImage(img) {
  imagePreviewSrc.value = imageUrl(img)
  imagePreviewVisible.value = true
}

const hasBacklinks = computed(() => (backlinks.value.incoming?.length || 0) + (backlinks.value.outgoing?.length || 0) > 0)

function relLabel(t) {
  const map = { similar: '相似', keyword: '关键词', reference: '引用', same_entity: '同实体' }
  return map[t] || t
}

// 按章节（metadata.heading）分组，生成「章节标题 + 内容块」的可阅读结构
const sectionedChunks = computed(() => {
  const sections = []
  let current = { heading: '', chunks: [] }
  for (const c of chunks.value) {
    let heading = ''
    try {
      const meta = typeof c.metadata === 'string' ? JSON.parse(c.metadata) : (c.metadata || {})
      heading = meta.heading || ''
    } catch { heading = '' }
    if (heading && heading !== current.heading) {
      if (current.chunks.length) sections.push(current)
      current = { heading, chunks: [] }
    }
    current.chunks.push(c)
  }
  if (current.chunks.length) sections.push(current)
  return sections
})

// 清洗 chunk 内容：去除页眉页脚噪声（Page N / Confidential / 日期 / 重复标题），保留可读正文
function cleanContent(text) {
  if (!text) return ''
  let lines = text.split('\n')
  // 逐行过滤噪声
  lines = lines.filter(line => {
    const t = line.trim()
    if (!t) return false  // 空行（后续 markdown 重新生成间距）
    // 页眉页脚噪声：页码、Confidential、日期、重复的眉题
    if (/^Page\s*\d+$/i.test(t)) return false
    if (/^\d{1,2}\/\d{1,2}[’'‘]\d{2}$/.test(t)) return false  // 10/11'99 日期
    if (/^(Confidential|Design Guide|Prepared|Revision)\b/i.test(t)) return false
    if (/^(Foxconn|連接器設計手冊|Design Guide for Connector)$/i.test(t)) return false
    return true
  })
  return lines.join('\n')
}

// Markdown 渲染（marked + DOMPurify 防 XSS）
function renderMarkdown(text) {
  if (!text) return ''
  try {
    const html = marked.parse(text, { breaks: true, gfm: true })
    return DOMPurify.sanitize(html)
  } catch {
    // 渲染失败降级为纯文本
    return text.replace(/</g, '&lt;').replace(/\n/g, '<br/>')
  }
}

function setChunkRef(idx, el) {
  if (el) chunkEls[idx] = el
}

// 加载所有 chunk 的被引用次数（一次批量，供「被引用 N 次」角标前置显示）
async function loadChunkRefCounts() {
  try {
    const { data } = await api.get(`/documents/${route.params.id}/chunk-refs`)
    chunkRefCounts.value = data.data || {}
  } catch { chunkRefCounts.value = {} }
}

// 展开/收起某个 chunk 的引用来源（懒加载：点开才拉详情）
async function toggleChunkRefs(chunkId) {
  if (expandedChunkRefs.value === chunkId) {
    expandedChunkRefs.value = null
    return
  }
  expandedChunkRefs.value = chunkId
  if (!chunkRefsDetail.value[chunkId]) {
    refsLoading.value[chunkId] = true
    try {
      const { data } = await api.get(`/chunks/${chunkId}/refs`)
      chunkRefsDetail.value[chunkId] = data.data || []
    } catch {
      chunkRefsDetail.value[chunkId] = []
    } finally {
      refsLoading.value[chunkId] = false
    }
  }
}

// 跳转到引用该 chunk 的对话（带会话 id）
function goToConversation(convId) {
  if (!convId) return
  router.push({ path: '/', query: { conversation: convId } })
}

function iconFor(ext) {
  const map = { '.pdf': '📄', '.ppt': '📊', '.pptx': '📊', '.xlsx': '📈', '.xls': '📈', '.docx': '📝', '.doc': '📝' }
  return map[ext?.toLowerCase()] || '📋'
}

// 等宽扩展名徽标（与 DocumentsView 一致的工业精工风格）
function extLabel(ext) {
  const e = (ext || '').replace('.', '').toUpperCase()
  const short = { PPTX: 'PPT', XLSX: 'XLS', XLS: 'XLS', DOCX: 'DOC', DOC: 'DOC' }
  return short[e] || e || 'FILE'
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

// 管理操作：删除 / 改分类 / 改标签（补齐后端已实现但前端缺失的 U/D 链路）
async function onAction(cmd) {
  if (cmd === 'delete') {
    try {
      await ElMessageBox.confirm(`确定删除「${file.value.name}」吗？删除后不可恢复。`, '删除确认', {
        type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消'
      })
    } catch { return }  // 用户取消
    try {
      await api.delete(`/documents/${file.value.id}`)
      ElMessage.success('已删除')
      router.push('/documents')
    } catch (e) {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  } else if (cmd === 'edit-category') {
    try {
      const { value } = await ElMessageBox.prompt('输入新分类', '修改分类', {
        inputValue: file.value.category || '', confirmButtonText: '确定', cancelButtonText: '取消'
      })
      if (value == null || value.trim() === '') return
      await api.put(`/documents/${file.value.id}/category`, { category: value.trim() })
      file.value.category = value.trim()
      ElMessage.success('分类已更新')
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error('修改失败: ' + (e.response?.data?.detail || e.message))
    }
  } else if (cmd === 'edit-tags') {
    try {
      const { value } = await ElMessageBox.prompt('输入标签（逗号分隔）', '修改标签', {
        inputValue: (file.value.tags || []).join(','), confirmButtonText: '确定', cancelButtonText: '取消'
      })
      if (value == null) return
      const tags = value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
      await api.put(`/documents/${file.value.id}/tags`, { tags })
      file.value.tags = tags
      ElMessage.success('标签已更新')
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error('修改失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

// 用 WPS 本地客户端打开（方案 2：零成本 URI 唤起；后续可升 WebOffice 内嵌编辑）
function openWithWps() {
  if (!file.value) return
  const name = file.value.name
  const path = file.value.path || ''
  const wpsUri = 'wps://' + encodeURIComponent(path)
  try {
    const w = window.open(wpsUri, '_blank')
    if (!w) throw new Error('blocked')
  } catch {
    ElMessage.info('未检测到可用的 WPS 客户端。若已安装 WPS，可在文件所在目录用 WPS 打开。')
    return
  }
  ElMessage.success('已唤起 WPS 打开：' + name)
}

// 导出完整 Markdown（调用后端 /markdown 端点，下载 .md 文件）
async function exportMarkdown() {
  if (!file.value) return
  try {
    const { data } = await api.get(`/documents/${route.params.id}/markdown`)
    const md = data.data.markdown || ''
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = (file.value.name.replace(/\.[^.]+$/, '') || 'document') + '.md'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出 Markdown')
  } catch (e) {
    ElMessage.error('导出失败: ' + (e.response?.data?.detail || e.message))
  }
}

// 可读模式：加载完整连续 Markdown 文档流
async function loadFullMarkdown() {
  if (!file.value || fullMarkdown.value) return
  fullMarkdownLoading.value = true
  try {
    const { data } = await api.get(`/documents/${route.params.id}/markdown`)
    fullMarkdown.value = data.data.markdown || ''
  } catch (e) {
    fullMarkdown.value = ''
    ElMessage.warning('加载完整文档失败，已回退到章节视图')
    readMode.value = 'section'
  } finally {
    fullMarkdownLoading.value = false
  }
}

// 加载图片列表（可读模式 ![[图]] 显示）
async function loadImages() {
  if (!file.value) return
  try {
    const { data } = await api.get(`/documents/${route.params.id}/images`)
    images.value = data.data || []
  } catch { images.value = [] }
}

// 判断是否为 Markdown 源（.md 文件），可读模式直接渲染原始语义
const isMarkdownSource = computed(() => {
  if (!chunks.value.length) return false
  try {
    const meta = typeof chunks.value[0].metadata === 'string'
      ? JSON.parse(chunks.value[0].metadata) : (chunks.value[0].metadata || {})
    return !!meta.markdown
  } catch { return false }
})

// 图片 URL（静态托管 /images/{file_id}/{filename}）
function imageUrl(img) {
  return '/' + (img.path || '')
}

// 按页分组图片（供可读模式穿插）
const imagesByPage = computed(() => {
  const map = {}
  images.value.forEach(img => {
    const p = img.page ?? 0
    ;(map[p] = map[p] || []).push(img)
  })
  return map
})

watch(readMode, (v) => {
  if (v === 'read') loadFullMarkdown()
})

async function loadFile() {
  loadError.value = ''
  file.value = null
  chunks.value = []
  fullMarkdown.value = ''
  dmsRecord.value = null
  try {
    const { data } = await api.get(`/documents/${route.params.id}`)
    file.value = data.data.file
    chunks.value = data.data.chunks || []
    // SeedDMS 来源反查（若该文件是从 DMS 导入的）
    try {
      const dmsRes = await dmsApi.recordByFile(route.params.id)
      if (dmsRes.data?.data) dmsRecord.value = dmsRes.data.data
    } catch { dmsRecord.value = null }
    // 关联文件（反向链接）
    try {
      const bl = await api.get(`/documents/${route.params.id}/backlinks`)
      backlinks.value = bl.data.data || { incoming: [], outgoing: [] }
    } catch { backlinks.value = { incoming: [], outgoing: [] } }
    // 加载图片列表
    loadImages()
    // 加载 chunk 被引用次数（第 2 项反向联动）
    loadChunkRefCounts()
    // 锚点定位：跳转到指定段落
    const chunkParam = route.query.chunk
    if (chunkParam !== undefined && chunkParam !== null && chunkParam !== '') {
      const idx = Number(chunkParam)
      highlightChunk.value = idx
      await nextTick()
      const el = chunkEls[idx]
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  } catch (e) {
    console.error('加载文档失败', e)
    loadError.value = '文档加载失败：' + (e.response?.data?.detail || e.message || '网络错误')
  }
}

onMounted(loadFile)
</script>

<style scoped>
/* 文档头部：扩展名徽标 + 文件名 + 元数据 */
.doc-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.doc-head-icon {
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: var(--radius);
  background: var(--bg-tertiary);
  display: flex;
  align-items: center;
  justify-content: center;
}
.doc-head-ext {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: var(--accent);
}
.doc-head-info {
  flex: 1;
  min-width: 0;
}
.doc-head-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-head-meta {
  font-size: 12px;
  color: var(--text-tertiary);
}
.doc-head-dms {
  margin-left: 8px;
  cursor: pointer;
}
.doc-head-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 文档正文：章节化 + Markdown 可阅读渲染 */
.doc-body {
  max-width: 820px;
  margin: 0 auto;
}
.doc-section {
  margin-bottom: 24px;
}
.doc-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  padding: 10px 14px;
  margin-bottom: 10px;
  background: var(--bg-subtle, #f5f6f8);
  border-left: 3px solid var(--accent, #4f46e5);
  border-radius: 6px;
}
.doc-section-icon {
  color: var(--accent, #4f46e5);
  font-size: 14px;
}
.doc-section-count {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: auto;
}
.chunk-block {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 10px);
  padding: 16px 20px;
  margin-bottom: 10px;
  transition: box-shadow 0.3s, border-color 0.3s;
}
.chunk-block--highlight {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px var(--accent-glow);
}

/* 第 2 项：chunk 被引用角标 + 展开面板（反向联动，工业精工克制风格） */
.chunk-ref-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-light);
  border: 1px solid var(--accent-glow);
  border-radius: 999px;
  padding: 3px 10px;
  margin-bottom: 8px;
  cursor: pointer;
  font-family: var(--font-mono);
  letter-spacing: 0.3px;
  transition: background var(--duration-fast), border-color var(--duration-fast);
}
.chunk-ref-badge:hover {
  background: var(--accent-glow);
  border-color: var(--accent);
}
.chunk-ref-arrow {
  font-size: 12px;
  margin-left: 2px;
}
.chunk-ref-panel {
  margin-bottom: 10px;
  padding: 8px 12px;
  background: var(--bg-subtle, #f5f6f8);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.chunk-ref-loading,
.chunk-ref-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 6px 0;
  font-family: var(--font-mono);
}
.chunk-ref-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 0;
  font-size: 12.5px;
  cursor: pointer;
  color: var(--text-secondary);
  border-bottom: 1px dashed var(--border);
}
.chunk-ref-item:last-child { border-bottom: none; }
.chunk-ref-item:hover { color: var(--accent); }
.chunk-ref-query {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.chunk-ref-meta {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  flex-shrink: 0;
}
/* Markdown 渲染内容样式 */
.chunk-markdown {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-primary);
  word-break: break-word;
}
.chunk-markdown :deep(h1),
.chunk-markdown :deep(h2),
.chunk-markdown :deep(h3),
.chunk-markdown :deep(h4) {
  margin: 0.6em 0 0.4em;
  font-weight: 700;
  line-height: 1.4;
}
.chunk-markdown :deep(h1) { font-size: 1.4em; }
.chunk-markdown :deep(h2) { font-size: 1.25em; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
.chunk-markdown :deep(h3) { font-size: 1.1em; }
.chunk-markdown :deep(p) { margin: 0.4em 0; }
.chunk-markdown :deep(ul),
.chunk-markdown :deep(ol) { margin: 0.4em 0; padding-left: 1.6em; }
.chunk-markdown :deep(li) { margin: 0.2em 0; }
.chunk-markdown :deep(strong) { font-weight: 700; color: var(--text-primary); }
.chunk-markdown :deep(code) {
  background: var(--bg-subtle, #f5f6f8);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 0.9em;
}
.chunk-markdown :deep(table) { border-collapse: collapse; margin: 0.6em 0; width: 100%; }
.chunk-markdown :deep(th),
.chunk-markdown :deep(td) { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.chunk-markdown :deep(th) { background: var(--bg-subtle, #f5f6f8); font-weight: 600; }
.chunk-markdown :deep(blockquote) {
  border-left: 3px solid var(--border);
  margin: 0.6em 0;
  padding: 0.2em 1em;
  color: var(--text-secondary);
}

/* 图片面板（可读模式 ![[图]] 展示） */
.full-md {
  max-width: 100%;
}
.images-panel {
  margin-top: 16px;
}
.images-panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
}
.images-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
.image-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-subtle, #f7f8fa);
  cursor: zoom-in;
  transition: box-shadow 0.2s;
}
.image-card:hover {
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
}
.image-card img {
  width: 100%;
  height: 120px;
  object-fit: cover;
  display: block;
}
.image-meta {
  padding: 4px 8px;
  font-size: 11px;
  color: var(--text-tertiary);
}
</style>
