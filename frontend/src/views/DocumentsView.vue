<template>
  <div style="display:flex;height:100%;overflow:hidden">
    <!-- 左侧分类 -->
    <div style="width:200px;min-width:200px;background:var(--bg-primary);border-right:1px solid var(--border);padding:12px;overflow-y:auto">
      <el-input v-model="searchText" placeholder="搜索文档..." size="small" style="margin-bottom:12px" clearable />
      <div style="font-size:12px;color:var(--text-tertiary);margin-bottom:8px;font-weight:600">分类</div>
      <div class="category-tree">
        <div class="category-item"
          :class="{ active: activeCategory === '' }"
          @click="activeCategory = ''">
          <el-icon><Folder /></el-icon> 全部
          <span class="category-count">{{ files.length }}</span>
        </div>
        <div class="category-item"
          v-for="cat in categories"
          :key="cat.name"
          :class="{ active: activeCategory === cat.name }"
          @click="activeCategory = cat.name">
          <el-icon><Folder /></el-icon> {{ cat.name }}
          <span class="category-count">{{ cat.count }}</span>
        </div>
      </div>
    </div>

    <!-- 右侧文档区 -->
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:12px 16px;background:var(--bg-primary);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-weight:600">{{ activeCategory || '全部文档' }}</span>
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".pdf,.ppt,.pptx,.xlsx,.xls,.docx,.doc,.txt,.md"
          style="display:none"
          @change="onFilesSelected"
        />
        <input
          ref="folderInput"
          type="file"
          webkitdirectory
          multiple
          accept=".pdf,.ppt,.pptx,.xlsx,.xls,.docx,.doc,.txt,.md"
          style="display:none"
          @change="onFilesSelected"
        />
        <el-button v-if="auth.isAdmin" type="primary" size="small" :icon="Upload" @click="$refs.fileInput.click()">上传文档</el-button>
        <el-button v-if="auth.isAdmin" size="small" :icon="Folder" @click="$refs.folderInput.click()" style="margin-left:8px">上传文件夹</el-button>
        <span v-if="uploading" style="font-size:12px;color:var(--accent)">{{ uploadProgress }}</span>
      </div>

      <!-- 字段筛选栏：型号/材料/日期（阶段 3） -->
      <div style="padding:8px 16px;background:var(--bg-secondary);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:12px;color:var(--text-tertiary)">筛选：</span>
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

        <div style="flex:1"></div>
        <!-- 视图切换：卡片 / 表格 -->
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button value="card">卡片</el-radio-button>
          <el-radio-button value="table">表格</el-radio-button>
        </el-radio-group>
      </div>

      <div style="flex:1;overflow-y:auto">
        <div v-if="filteredFiles.length === 0" class="empty-state">
          <el-icon><Folder /></el-icon>
          <p>暂无文档</p>
          <p style="font-size:12px">上传 PDF、PPT、XLSX 或 DOCX 文件开始使用</p>
        </div>

        <!-- 表格视图 -->
        <el-table v-else-if="viewMode === 'table'" :data="filteredFiles" style="width:100%" @row-click="row => $router.push(`/document/${row.id}`)" row-class-name="clickable-row">
          <el-table-column prop="name" label="文件名" min-width="240" show-overflow-tooltip />
          <el-table-column prop="category" label="分类" width="110" />
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
        </el-table>

        <!-- 卡片视图 -->
        <div class="doc-list" v-else>
          <div class="doc-card" v-for="f in filteredFiles" :key="f.id" @click="$router.push(`/document/${f.id}`)">
            <div class="doc-card-header">
              <div class="doc-card-icon" :style="iconStyle(f.ext)">
                {{ iconFor(f.ext) }}
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
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
              <span class="tag" v-if="f.category">{{ f.category }}</span>
              <span class="tag tag-model" v-for="m in (f.models || []).slice(0, 4)" :key="'m'+m">{{ m }}</span>
              <span class="tag tag-material" v-for="m in (f.materials || []).slice(0, 4)" :key="'mat'+m">{{ m }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Upload, Folder } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const files = ref([])
const searchText = ref('')
const activeCategory = ref('')
const uploading = ref(false)
const uploadProgress = ref('')
const filterModel = ref('')
const filterMaterial = ref('')
const filterDate = ref('')
const viewMode = ref('card')

const categories = computed(() => {
  const map = {}
  files.value.forEach(f => {
    const cat = f.category || '未分类'
    map[cat] = (map[cat] || 0) + 1
  })
  return Object.entries(map).map(([name, count]) => ({ name, count }))
})

const filteredFiles = computed(() => {
  let list = files.value
  if (activeCategory.value) list = list.filter(f => (f.category || '未分类') === activeCategory.value)
  if (searchText.value) {
    const s = searchText.value.toLowerCase()
    list = list.filter(f => f.name.toLowerCase().includes(s))
  }
  if (filterModel.value) list = list.filter(f => (f.models || []).includes(filterModel.value))
  if (filterMaterial.value) list = list.filter(f => (f.materials || []).includes(filterMaterial.value))
  if (filterDate.value) {
    const days = parseInt(filterDate.value)
    const cutoff = Date.now() - days * 24 * 3600 * 1000
    list = list.filter(f => {
      const t = f.updated_at || f.created_at
      if (!t) return false
      return new Date(t) >= cutoff
    })
  }
  return list
})

// 所有可筛的型号/材料（去重，来自当前文件列表）
const allModels = computed(() => {
  const s = new Set()
  files.value.forEach(f => (f.models || []).forEach(m => s.add(m)))
  return Array.from(s).sort()
})
const allMaterials = computed(() => {
  const s = new Set()
  files.value.forEach(f => (f.materials || []).forEach(m => s.add(m)))
  return Array.from(s).sort()
})

function onFilterChange() {}
function clearFilter() {
  filterModel.value = ''
  filterMaterial.value = ''
  filterDate.value = ''
}

function formatDate(ts) {
  if (!ts) return ''
  // SQLite datetime('now','localtime') 格式：YYYY-MM-DD HH:MM:SS
  const s = String(ts)
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 16)
  const d = new Date(ts)
  if (isNaN(d)) return s
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function iconFor(ext) {
  const map = { '.pdf': '📄', '.ppt': '📊', '.pptx': '📊', '.xlsx': '📈', '.xls': '📈', '.docx': '📝', '.doc': '📝' }
  return map[ext?.toLowerCase()] || '📋'
}

function iconStyle(ext) {
  const colors = { '.pdf': '#fff0f0', '.ppt': '#fff8e1', '.pptx': '#fff8e1', '.xlsx': '#e8f5e9', '.xls': '#e8f5e9', '.docx': '#e3f2fd', '.doc': '#e3f2fd' }
  return { background: colors[ext?.toLowerCase()] || 'var(--bg-tertiary)' }
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

async function fetchFiles() {
  try {
    const { data } = await api.get('/documents')
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

async function uploadFilesSeq(filesList, idx) {
  if (idx >= filesList.length) {
    ElMessage.success(`${filesList.length} 个文件已提交处理`)
    return
  }
  const file = filesList[idx]
  const form = new FormData()
  form.append('file', file)
  uploading.value = true
  uploadProgress.value = `提交: ${file.name}`
  try {
    const { data } = await api.post('/documents/upload', form)
    const taskId = data.data.task_id
    ElMessage.info(`${file.name} 已加入处理队列`)
    // 启动进度轮询
    pollProgress(taskId, file.name)
  } catch (e) {
    ElMessage.error(`${file.name} 提交失败: ` + (e.response?.data?.detail || e.message))
    uploading.value = false
    uploadProgress.value = ''
  }
  // 继续下一个文件
  setTimeout(() => uploadFilesSeq(filesList, idx + 1), 300)
}

async function pollProgress(taskId, filename) {
  const maxAttempts = 600 // 最多轮询 30 分钟
  let attempts = 0
  let consecutiveFails = 0 // 连续失败计数（用于熔断告警）
  while (attempts < maxAttempts) {
    await new Promise(r => setTimeout(r, 3000)) // 每 3 秒查一次
    attempts++
    try {
      const { data } = await api.get(`/documents/upload/${taskId}/progress`)
      consecutiveFails = 0 // 成功即清零
      const s = data.data
      uploadProgress.value = `${filename}: ${s.progress_text} (${s.progress}%)`
      if (s.status === 'done') {
        ElMessage.success(`${filename} 处理完成 (${s.chunks} 块)`)
        uploading.value = false
        uploadProgress.value = ''
        await fetchFiles()
        return
      }
      if (s.status === 'failed') {
        ElMessage.error(`${filename} 处理失败: ${s.error}`)
        uploading.value = false
        uploadProgress.value = ''
        return
      }
    } catch (e) {
      // 单次断线/超时静默重试；连续 10 次（约 30s）才提示，避免误报
      consecutiveFails++
      if (consecutiveFails === 10) {
        ElMessage.warning(`${filename} 进度查询不稳定，正在重连...`)
      }
    }
  }
  uploading.value = false
  uploadProgress.value = ''
  ElMessage.warning(`${filename} 处理超时，请刷新页面查看`)
}

onMounted(fetchFiles)
</script>
