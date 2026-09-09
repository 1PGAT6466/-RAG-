<template>
  <div class="docs-page">
    <!-- 左侧目录树 + 分类 -->
    <aside class="docs-sidebar">
      <!-- AI 智能找文件 -->
      <div class="find-box">
        <el-input
          v-model="findText"
          placeholder="AI 找文件：描述你想找的内容..."
          size="small"
          clearable
          @keyup.enter="doFindFile"
        >
          <template #prefix><el-icon><MagicStick /></el-icon></template>
        </el-input>
        <el-button size="small" type="primary" :icon="Search" :loading="finding" @click="doFindFile" style="margin-top:6px;width:100%">智能找文件</el-button>
      </div>

      <el-input v-model="searchText" placeholder="文件名搜索..." size="small" class="docs-search" clearable>
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <!-- 目录树（Obsidian 式文件夹） -->
      <div class="docs-sidebar-label">目录</div>
      <el-tree
        :data="folderTree"
        :props="{ label: 'name', children: 'children' }"
        node-key="path"
        :expand-on-click-node="false"
        highlight-current
        @node-click="onFolderClick"
      >
        <template #default="{ data }">
          <span class="folder-node">
            <el-icon><Folder /></el-icon>
            <span class="folder-name">{{ data.name }}</span>
            <span class="category-count">{{ data.count }}</span>
          </span>
        </template>
      </el-tree>

      <div class="docs-sidebar-label" style="margin-top:16px">分类</div>
      <div class="category-tree">
        <div class="category-item"
          :class="{ active: activeCategory === '' && activeFolder === '' }"
          @click="selectCategory('')">
          <el-icon><Folder /></el-icon> 全部
          <span class="category-count">{{ files.length }}</span>
        </div>
        <div class="category-item"
          v-for="cat in categories"
          :key="cat.name"
          :class="{ active: activeCategory === cat.name }"
          @click="selectCategory(cat.name)">
          <el-icon><Folder /></el-icon> {{ cat.name }}
          <span class="category-count">{{ cat.count }}</span>
        </div>
      </div>
    </aside>

    <!-- 右侧文档区 -->
    <section class="docs-main">
      <div class="docs-toolbar">
        <span class="docs-toolbar-title">{{ activeCategory || '全部文档' }}</span>
        <input
          ref="fileInputRef"
          type="file"
          multiple
          accept=".pdf,.ppt,.pptx,.xlsx,.xls,.docx,.doc,.txt,.md"
          style="display:none"
          @change="onFilesSelected"
        />
        <input
          ref="folderInputRef"
          type="file"
          webkitdirectory
          multiple
          accept=".pdf,.ppt,.pptx,.xlsx,.xls,.docx,.doc,.txt,.md"
          style="display:none"
          @change="onFilesSelected"
        />
        <el-button v-if="auth.isAdmin" type="primary" size="small" :icon="Upload" @click="openDmsFolderDialog('file')">上传文档</el-button>
        <el-button v-if="auth.isAdmin" size="small" :icon="Folder" @click="openDmsFolderDialog('folder')">上传文件夹</el-button>
        <el-button v-if="auth.isAdmin" size="small" :icon="Plus" @click="newFolderVisible = true">新建文件夹</el-button>
        <span v-if="uploading" class="docs-upload-status">{{ uploadProgress }}</span>
      </div>

      <!-- 后台向量化进度（上传后展示） -->
      <div v-if="vectorizeJobs.length" class="docs-vectorize-panel">
        <div v-for="job in vectorizeJobs" :key="job.dms_doc_id" class="docs-vectorize-item">
          <div class="docs-vectorize-head">
            <span class="docs-vectorize-name">{{ job.filename }}</span>
            <span class="docs-vectorize-state" :class="job.job_status">
              {{ jobStatusText(job) }}
            </span>
          </div>
          <el-progress
            :percentage="job.progress"
            :status="job.job_status === 'done' ? 'success' : job.job_status === 'failed' ? 'exception' : undefined"
            :stroke-width="8"
          />
          <div class="docs-vectorize-detail" v-if="job.progress_text">{{ job.progress_text }}</div>
        </div>
      </div>

      <!-- AI 找文件结果抽屉 -->
      <el-drawer v-model="findVisible" title="AI 找文件结果" size="420px">
        <EmptyState v-if="findResults.length === 0" icon="Search" title="未找到相关文件" hint="换个说法试试" />
        <div v-else class="find-result-item" v-for="f in findResults" :key="f.file_id" @click="goFile(f.file_id, f.snippet_chunk_index)">
          <div class="find-result-name">{{ f.file_name }}</div>
          <div class="find-result-reason" v-if="f.reason">{{ f.reason }}</div>
          <div class="find-result-meta">命中 {{ f.hits }} 处 · 相关度 {{ (f.score * 100).toFixed(0) }}%</div>
        </div>
      </el-drawer>

      <!-- 新建文件夹弹窗 -->
      <el-dialog v-model="newFolderVisible" title="新建文件夹" width="360px">
        <el-input v-model="newFolderName" placeholder="文件夹名，如：连接器资料" @keyup.enter="createFolder" />
        <template #footer>
          <el-button @click="newFolderVisible = false">取消</el-button>
          <el-button type="primary" @click="createFolder">创建</el-button>
        </template>
      </el-dialog>

      <!-- 选择 DMS 目标文件夹（上传前） -->
      <el-dialog v-model="dmsFolderVisible" title="选择 DMS 目标文件夹" width="460px">
        <p class="docs-dms-tip">上传的文件将作为原件存入 SeedDMS，并向量化入库。</p>
        <el-select
          v-model="targetDmsFolderId"
          placeholder="选择 SeedDMS 文件夹"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="f in dmsFolders"
            :key="f.id"
            :label="formatDmsFolderLabel(f)"
            :value="f.id"
          />
        </el-select>
        <template #footer>
          <el-button @click="dmsFolderVisible = false">取消</el-button>
          <el-button type="primary" :loading="dmsFolderLoading" @click="confirmFolderAndPick">
            选择文件
          </el-button>
        </template>
      </el-dialog>

      <!-- 字段筛选栏：型号/材料/日期 -->
      <div class="docs-filter-bar">
        <span class="docs-filter-label">筛选</span>
        <el-select v-model="filterModel" placeholder="型号" clearable filterable size="small" style="width:160px" @change="onFilterChange">
          <el-option v-for="m in allModels" :key="m" :label="m" :value="m" />
        </el-select>
        <el-select v-model="filterMaterial" placeholder="材料" clearable filterable size="small" style="width:160px" @change="onFilterChange">
          <el-option v-for="m in allMaterials" :key="m" :label="m" :value="m" />
        </el-select>
        <el-select v-model="filterDate" placeholder="日期" clearable size="small" style="width:120px" @change="onFilterChange">
          <el-option label="全部" value="" />
          <el-option label="近 7 天" value="7d" />
          <el-option label="近 30 天" value="30d" />
          <el-option label="近 90 天" value="90d" />
        </el-select>
        <el-button size="small" text type="primary" @click="clearFilter">重置</el-button>

        <div class="docs-filter-spacer"></div>
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button value="card">卡片</el-radio-button>
          <el-radio-button value="table">表格</el-radio-button>
        </el-radio-group>
      </div>

      <div class="docs-list-area">
        <EmptyState v-if="filteredFiles.length === 0" icon="Folder" title="暂无文档" hint="上传 PDF、PPT、XLSX 或 DOCX 文件开始使用" />

        <!-- 表格视图 -->
        <el-table v-else-if="viewMode === 'table'" :data="filteredFiles" style="width:100%" @row-click="row => $router.push(`/document/${row.id}`)" row-class-name="clickable-row">
          <el-table-column prop="name" label="文件名" min-width="240" show-overflow-tooltip />
          <el-table-column label="目录" width="120">
            <template #default="{ row }">
              <span class="folder-tag">{{ row.folder || '/' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="分类" width="110">
            <template #default="{ row }">
              <span class="cat-pill" :style="{ background: categoryColor(row.category) }">{{ row.category || '未分类' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="型号" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="m in (row.models || []).slice(0, 3)" :key="m" size="small" type="primary" style="margin-right:4px">{{ m }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="材料" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="m in (row.materials || []).slice(0, 3)" :key="m" size="small" type="success" style="margin-right:4px">{{ m }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="块数" width="70" align="center" />
          <el-table-column label="大小" width="90">
            <template #default="{ row }">{{ formatSize(row.size) }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="160">
            <template #default="{ row }">{{ formatDate(row.updated_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column v-if="auth.isAdmin" label="操作" width="100" align="center">
            <template #default="{ row }">
              <el-button size="small" text type="danger" :icon="Delete" @click.stop="deleteFile(row.id, row.name)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 卡片视图 -->
        <div class="doc-list" v-else>
          <div class="doc-card" v-for="f in filteredFiles" :key="f.id" @click="$router.push(`/document/${f.id}`)">
            <div class="doc-card-header">
              <div class="doc-card-icon" :style="iconStyle(f.ext)">
                <span class="doc-card-ext">{{ extLabel(f.ext) }}</span>
              </div>
              <div>
                <div class="doc-card-title">{{ f.name }}</div>
                <div class="doc-card-meta">
                  <span>{{ f.chunk_count }} 块</span>
                  <span>{{ formatSize(f.size) }}</span>
                  <span v-if="f.updated_at">{{ formatDate(f.updated_at) }}</span>
                </div>
              </div>
            </div>
            <div class="doc-card-tags">
              <span class="tag cat-pill" v-if="f.category" :style="{ background: categoryColor(f.category) }">{{ f.category }}</span>
              <span class="tag" v-if="f.folder && f.folder !== '/'">{{ f.folder }}</span>
              <span class="tag tag-model" v-for="m in (f.models || []).slice(0, 4)" :key="'m'+m">{{ m }}</span>
              <span class="tag tag-material" v-for="m in (f.materials || []).slice(0, 4)" :key="'mat'+m">{{ m }}</span>
            </div>
            <div class="doc-card-actions" v-if="auth.isAdmin">
              <el-button size="small" text type="danger" :icon="Delete" @click.stop="deleteFile(f.id, f.name)">删除</el-button>
              <el-dropdown @command="cmd => moveToFolder(f.id, cmd)" trigger="click">
                <el-button size="small" text :icon="MoreFilled" @click.stop>移动到</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="/">根目录</el-dropdown-item>
                    <el-dropdown-item v-for="fo in flatFolders" :key="fo.path" :command="fo.path">{{ fo.name }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Upload, Folder, Search, MagicStick, Plus, MoreFilled, Delete } from '@element-plus/icons-vue'
// ElMessage/ElMessageBox 由 unplugin-auto-import 自动引入（含样式）
import api from '../api'
import dmsApi from '../api/dms'
import { useAuthStore } from '../stores/auth'
import EmptyState from '../components/EmptyState.vue'
import { formatDate, iconFor, extLabel, iconStyle, formatSize } from './docs/helpers'
import { categoryColor } from '../constants/category'

const auth = useAuthStore()
const router = useRouter()
const files = ref([])
const searchText = ref('')
const activeCategory = ref('')
const activeFolder = ref('')
const uploading = ref(false)
const uploadProgress = ref('')
const filterModel = ref('')
const filterMaterial = ref('')
const filterDate = ref('')
const viewMode = ref('card')

// 目录树 + AI 找文件
const folderTree = ref([])
const flatFolders = ref([])
const findText = ref('')
const finding = ref(false)
const findVisible = ref(false)
const findResults = ref([])
const newFolderVisible = ref(false)
const newFolderName = ref('')
const fileInputRef = ref(null)
const folderInputRef = ref(null)
// 后台向量化进度任务列表（上传后轮询展示）
const vectorizeJobs = ref([])

// DMS 目标文件夹选择（上传前）
const dmsFolderVisible = ref(false)
const dmsFolders = ref([])
const targetDmsFolderId = ref(null)
const dmsFolderLoading = ref(false)
const pendingPickType = ref('file') // 'file' 或 'folder'

// allFiles：全量（供分类树 + 型号/材料下拉选项）；files：筛选后列表
const allFiles = ref([])

const categories = computed(() => {
  const map = {}
  allFiles.value.forEach(f => {
    const cat = f.category || '未分类'
    map[cat] = (map[cat] || 0) + 1
  })
  return Object.entries(map).map(([name, count]) => ({ name, count }))
})

const filteredFiles = computed(() => {
  let list = files.value
  // 仅本地文本搜索（后端无 name 搜索，且为即时输入过滤场景）
  if (searchText.value) {
    const s = searchText.value.toLowerCase()
    list = list.filter(f => f.name.toLowerCase().includes(s))
  }
  return list
})

// 所有可筛的型号/材料（去重，来自全量文件列表，仅用于下拉选项展示）
const allModels = computed(() => {
  const s = new Set()
  allFiles.value.forEach(f => (f.models || []).forEach(m => s.add(m)))
  return Array.from(s).sort()
})
const allMaterials = computed(() => {
  const s = new Set()
  allFiles.value.forEach(f => (f.materials || []).forEach(m => s.add(m)))
  return Array.from(s).sort()
})

// 筛选变化 → 重新请求后端（带 category/model/material/date 参数），统一筛选逻辑
function onFilterChange() {
  fetchFiles()
}

function selectCategory(name) {
  activeCategory.value = name
  activeFolder.value = ''
  fetchFiles()
}

function onFolderClick(data) {
  activeFolder.value = data.path
  activeCategory.value = ''
  fetchFiles()
}

function clearFilter() {
  filterModel.value = ''
  filterMaterial.value = ''
  filterDate.value = ''
  activeCategory.value = ''
  activeFolder.value = ''
  fetchFiles()
}

// 目录树：扁平化所有目录（供移动下拉）
function flattenFolders(nodes, prefix) {
  const out = []
  for (const n of nodes) {
    out.push({ name: prefix + n.name, path: n.path })
    if (n.children?.length) out.push(...flattenFolders(n.children, prefix + n.name + ' / '))
  }
  return out
}

async function fetchFolders() {
  try {
    const { data } = await api.get('/folders')
    folderTree.value = data.data || []
    flatFolders.value = flattenFolders(data.data || [], '')
  } catch (e) {
    // 目录树加载失败不阻断
  }
}

function goFile(id, chunkIdx) {
  const q = chunkIdx !== undefined ? { chunk: chunkIdx } : {}
  router.push({ path: `/document/${id}`, query: q })
}

async function doFindFile() {
  const q = findText.value.trim()
  if (!q) { ElMessage.warning('请输入你想找的内容描述'); return }
  finding.value = true
  try {
    const { data } = await api.post('/files/find', { query: q })
    findResults.value = data.data || []
    findVisible.value = true
    if (findResults.value.length === 0) ElMessage.info(data.message || '未找到相关文件')
  } catch (e) {
    ElMessage.error('找文件失败: ' + (e.userMessage || e.message))
  } finally {
    finding.value = false
  }
}

async function createFolder() {
  const name = newFolderName.value.trim()
  if (!name) { ElMessage.warning('请输入文件夹名'); return }
  // 创建一个空文件夹：用 add_file 之外的方式？这里通过移动第一个文件无法实现，
  // 改为：前端本地创建后，提示用户上传时选择该目录。
  // 当前目录树是「虚拟目录」，基于文件 folder 字段派生，无独立文件夹实体。
  // 因此「新建文件夹」实际是：记住目标路径，上传时归入。
  activeFolder.value = '/' + name.replace(/^\/+/, '')
  newFolderVisible.value = false
  newFolderName.value = ''
  ElMessage.success(`已选中目录「${activeFolder.value}」，后续上传的文件将归入此目录`)
}

async function moveToFolder(fileId, folder) {
  try {
    await api.put(`/documents/${fileId}/folder`, { folder })
    ElMessage.success('已移动')
    await fetchFiles()
    await fetchFolders()
    await fetchAllFiles()
  } catch (e) {
    ElMessage.error('移动失败: ' + (e.userMessage || e.message))
  }
}

async function deleteFile(fileId, fileName) {
  try {
    await ElMessageBox.confirm(`确定删除「${fileName}」吗？删除后不可恢复。`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消'
    })
  } catch { return }
  try {
    await api.delete(`/documents/${fileId}`)
    ElMessage.success(`已删除「${fileName}」`)
    await fetchFiles()
    await fetchFolders()
    await fetchAllFiles()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.userMessage || e.message))
  }
}


async function fetchAllFiles() {
  try {
    const { data } = await api.get('/documents')
    allFiles.value = data.data || []
  } catch (e) {
    // 全量选项加载失败不阻断列表（列表 fetchFiles 会单独报错）
  }
}

async function fetchFiles() {
  try {
    const params = {}
    if (activeCategory.value) params.category = activeCategory.value
    if (activeFolder.value) params.folder = activeFolder.value
    if (filterModel.value) params.model = filterModel.value
    if (filterMaterial.value) params.material = filterMaterial.value
    if (filterDate.value) params.date = filterDate.value
    const { data } = await api.get('/documents', { params })
    files.value = data.data || []
  } catch (e) {
    ElMessage.error('加载文档列表失败')
  }
}

function onFilesSelected(e) {
  const selectedFiles = Array.from(e.target.files)
  if (selectedFiles.length === 0) return
  uploadFilesSeq(selectedFiles, 0)
  e.target.value = ''
}

// === DMS 目标文件夹选择 ===
function formatDmsFolderLabel(f) {
  // folderList 形如 ":1:2:"，用其层级数量作缩进
  const depth = (f.folderList || '').split(':').filter(Boolean).length
  return '　'.repeat(depth) + f.name
}

async function openDmsFolderDialog(type) {
  pendingPickType.value = type
  dmsFolderLoading.value = true
  dmsFolderVisible.value = true
  targetDmsFolderId.value = null
  try {
    const { data } = await dmsApi.folders()
    dmsFolders.value = (data?.data || []).sort((a, b) => (a.id || 0) - (b.id || 0))
    // 默认选中根文件夹
    if (dmsFolders.value.length) targetDmsFolderId.value = dmsFolders.value[0].id
  } catch (e) {
    ElMessage.error('拉取 DMS 文件夹失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    dmsFolderLoading.value = false
  }
}

function confirmFolderAndPick() {
  if (!targetDmsFolderId.value) {
    ElMessage.warning('请先选择目标文件夹')
    return
  }
  dmsFolderVisible.value = false
  if (pendingPickType.value === 'folder') {
    folderInputRef.value.click()
  } else {
    fileInputRef.value.click()
  }
}

async function uploadFilesSeq(filesList, idx) {
  if (idx >= filesList.length) {
    ElMessage.success(`${filesList.length} 个文件已存入 DMS，正在后台向量化`)
    return
  }
  const file = filesList[idx]
  const form = new FormData()
  form.append('file', file)
  if (targetDmsFolderId.value) form.append('folder_id', targetDmsFolderId.value)
  uploading.value = true
  uploadProgress.value = `上传: ${file.name}（写入 DMS...）`
  try {
    const { data } = await api.post('/documents/upload', form)
    const dmsId = data.data?.dms_doc_id
    if (dmsId) {
      // 登记到向量化进度面板，并启动轮询
      addVectorizeJob(dmsId, file.name)
    } else {
      ElMessage.success(`${file.name} 已存入 DMS`)
    }
  } catch (e) {
    ElMessage.error(`${file.name} 上传失败: ` + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
    uploadProgress.value = ''
  }
  // 继续下一个文件
  setTimeout(() => uploadFilesSeq(filesList, idx + 1), 300)
}

// === 后台向量化进度轮询（可取消 + onUnmounted 清理） ===
let _pollAbort = null  // 全局 AbortController，组件卸载时取消所有轮询

onUnmounted(() => {
  if (_pollAbort) _pollAbort.abort()
})

function addVectorizeJob(dmsId, filename) {
  const job = {
    dms_doc_id: dmsId,
    filename,
    job_status: 'pending',
    progress: 0,
    progress_text: '等待向量化...',
  }
  vectorizeJobs.value.push(job)
  pollVectorizeJob(dmsId, job)
}

function removeVectorizeJob(dmsId) {
  const i = vectorizeJobs.value.findIndex(j => j.dms_doc_id === dmsId)
  if (i !== -1) vectorizeJobs.value.splice(i, 1)
}

function jobStatusText(job) {
  switch (job.job_status) {
    case 'done': return '已完成'
    case 'failed': return '失败'
    case 'running': return '向量化中'
    default: return '等待中'
  }
}

async function pollVectorizeJob(dmsId, job) {
  // 可取消轮询：组件卸载时 _pollAbort.abort() 终止所有等待中的 sleep
  if (!_pollAbort || _pollAbort.signal.aborted) _pollAbort = new AbortController()
  const signal = _pollAbort.signal

  const maxAttempts = 1800  // 最多约 60 分钟（2s × 1800）
  let attempts = 0
  while (attempts < maxAttempts && !signal.aborted) {
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 2000)
        signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason) }, { once: true })
      })
    } catch { break }  // 组件已卸载，退出轮询
    if (signal.aborted) break
    attempts++
    try {
      const { data } = await api.get(`/documents/upload/${dmsId}/vectorize-status`)
      const s = data.data
      job.job_status = s.job_status
      if (s.job_status === 'done') {
        job.progress = 100
        job.progress_text = '向量化完成'
        await fetchFiles()
        await fetchAllFiles()
        setTimeout(() => removeVectorizeJob(dmsId), 4000)
        return
      }
      if (s.job_status === 'failed') {
        job.progress = 100
        job.progress_text = s.error || '向量化失败'
        return
      }
      if (s.task_id) {
        try {
          const pr = await api.get(`/documents/upload/${s.task_id}/progress`)
          const st = pr.data.data
          job.job_status = 'running'
          job.progress = st.progress || 0
          job.progress_text = st.progress_text || st.stage || '向量化中...'
        } catch { /* engine 任务可能尚未就绪 */ }
      }
    } catch (e) {
      if (e.response?.status === 404) {
        job.progress_text = '任务记录不存在（可能服务重启）'
        break
      }
    }
  }
}

onMounted(() => {
  fetchAllFiles()
  fetchFolders()
})
</script>

<style scoped>
.docs-page {
  display: flex;
  height: 100%;
  overflow: hidden;
}

/* 左侧分类 */
.docs-sidebar {
  width: 208px;
  min-width: 208px;
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
  padding: 16px 12px;
  overflow-y: auto;
}
.docs-search {
  margin-bottom: 16px;
}
.docs-sidebar-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

/* 右侧主区 */
.docs-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

/* 顶部工具栏 */
.docs-toolbar {
  padding: 16px 20px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.docs-toolbar-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  margin-right: 8px;
  font-family: var(--font-display);
  letter-spacing: 0.3px;
}
.docs-upload-status {
  font-size: 12px;
  color: var(--accent);
  font-weight: 500;
}

.docs-dms-tip {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-secondary, #666);
  line-height: 1.5;
}

/* 后台向量化进度面板 */
.docs-vectorize-panel {
  padding: 10px 20px;
  background: var(--bg-subtle, #f5f6f8);
  border-bottom: 1px solid var(--border);
}
.docs-vectorize-item {
  padding: 8px 0;
}
.docs-vectorize-item + .docs-vectorize-item {
  border-top: 1px dashed var(--border);
}
.docs-vectorize-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.docs-vectorize-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 75%;
}
.docs-vectorize-state {
  font-size: 12px;
  color: var(--text-secondary, #666);
}
.docs-vectorize-state.running {
  color: var(--accent);
}
.docs-vectorize-state.done {
  color: var(--success, #1c8a5a);
}
.docs-vectorize-state.failed {
  color: var(--danger, #d14e4e);
}
.docs-vectorize-detail {
  font-size: 12px;
  color: var(--text-secondary, #888);
  margin-top: 2px;
}

/* 筛选栏 */
.docs-filter-bar {
  padding: 10px 20px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.docs-filter-label {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 600;
}
.docs-filter-spacer {
  flex: 1;
}

/* 列表区 */
.docs-list-area {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.doc-card-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.doc-card-ext {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

/* 分类 pill：按分类着色（category.js 单一权威源） */
.cat-pill {
  color: #fff;
  font-weight: 600;
  transition: opacity 0.2s;
}
.cat-pill:hover { opacity: 0.88; }

.empty-sub {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* AI 找文件 */
.find-box {
  margin-bottom: 12px;
  padding: 8px;
  background: var(--bg-subtle, #f7f8fa);
  border-radius: 8px;
  border: 1px dashed var(--border);
}
.find-result-item {
  padding: 12px 14px;
  margin-bottom: 8px;
  background: var(--bg-subtle, #f7f8fa);
  border-radius: 8px;
  cursor: pointer;
  transition: box-shadow 0.2s;
}
.find-result-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.find-result-name {
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.find-result-reason {
  font-size: 12px;
  color: var(--accent);
  margin-bottom: 4px;
}
.find-result-meta {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 目录树 */
.folder-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  flex: 1;
}
.folder-name {
  flex: 1;
}
.folder-tag {
  font-size: 12px;
  color: var(--text-secondary);
}
.doc-card-actions {
  margin-top: 8px;
  border-top: 1px dashed var(--border);
  padding-top: 6px;
}
</style>
