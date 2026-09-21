<template>
  <el-dialog
    v-model="visible"
    :title="(isDmsMode ? 'DMS 预览：' : '预览：') + (effectiveName || '')"
    width="85%"
    top="3vh"
    append-to-body
    destroy-on-close
    @close="onClose"
  >
    <div v-if="loading" class="preview-loading">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>加载中…</span>
    </div>
    <div v-else-if="error" class="preview-error">
      <el-icon :size="32"><CircleClose /></el-icon>
      <p>{{ error }}</p>
    </div>
    <!-- Word 文档 -->
    <div v-else-if="isWord" ref="wordContainer" class="preview-word"></div>
    <!-- Excel 表格（分页：每页 100 行，避免大数据卡顿） -->
    <div v-else-if="isExcel" class="preview-excel">
      <el-tabs v-model="activeSheet" type="card">
        <el-tab-pane v-for="name in sheetNames" :key="name" :label="name" :name="name" />
      </el-tabs>
      <div class="excel-toolbar">
        <span class="excel-info">共 {{ currentSheetData.length }} 行 × {{ maxCols }} 列</span>
        <el-pagination
          v-if="currentSheetData.length > EXCEL_PAGE_SIZE"
          v-model:current-page="excelPage"
          :page-size="EXCEL_PAGE_SIZE"
          :total="currentSheetData.length"
          layout="prev, pager, next"
          small
        />
      </div>
      <div class="excel-table-wrap">
        <table class="excel-table">
          <tr v-for="(row, ri) in pagedExcelRows" :key="ri">
            <td v-for="(cell, ci) in row.slice(0, 20)" :key="ci" :class="{ 'excel-header': excelPage === 1 && ri === 0 }">{{ cell }}</td>
          </tr>
        </table>
      </div>
    </div>
    <!-- PDF（pdf.js 按页渲染，toolbar 内嵌） -->
    <div v-else-if="isPdf" class="preview-pdf">
      <iframe :src="pdfUrl" frameborder="0" style="background:#525659"></iframe>
    </div>
    <!-- 其他格式 -->
    <div v-else class="preview-unsupported">
      <el-icon :size="48"><Document /></el-icon>
      <p>该文件格式（{{ ext }}）暂不支持在线预览</p>
      <p class="preview-hint">支持格式：Word（.docx）、Excel（.xlsx/.xls）、PDF（.pdf）</p>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { Loading, CircleClose, Document } from '@element-plus/icons-vue'
import api from '../api'

const props = defineProps({
  modelValue: Boolean,
  fileId: Number,
  fileName: String,
  ext: String,
  dmsDocId: Number,       // SeedDMS 文档 ID（未导入伏羲的 DMS 文档）
  dmsFileName: String,    // SeedDMS 文件名
  dmsExt: String,         // SeedDMS 文件扩展名
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const error = ref('')
const wordContainer = ref(null)
const sheetNames = ref([])
const activeSheet = ref('')
const sheetsData = ref({})
const pdfUrl = ref('')

const extLower = computed(() => {
  const ext = props.dmsExt || props.ext || ''
  return ext.toLowerCase().replace('.', '')
})
const effectiveName = computed(() => props.dmsFileName || props.fileName || '')
const isWord = computed(() => ['docx'].includes(extLower.value))
const isExcel = computed(() => ['xlsx', 'xls'].includes(extLower.value))
const isPdf = computed(() => extLower.value === 'pdf')
const isDmsMode = computed(() => !!props.dmsDocId)

const currentSheetData = computed(() => sheetsData.value[activeSheet.value] || [])
const maxCols = computed(() => {
  const data = currentSheetData.value
  let max = 0
  for (let i = 0; i < Math.min(data.length, 100); i++) {
    if (data[i] && data[i].length > max) max = data[i].length
  }
  return max
})

// Excel 分页
const EXCEL_PAGE_SIZE = 100
const excelPage = ref(1)
const pagedExcelRows = computed(() => {
  const data = currentSheetData.value
  const start = (excelPage.value - 1) * EXCEL_PAGE_SIZE
  return data.slice(start, start + EXCEL_PAGE_SIZE)
})
watch(activeSheet, () => { excelPage.value = 1 })
watch(excelPage, () => {
  nextTick(() => {
    const wrap = document.querySelector('.excel-table-wrap')
    if (wrap) wrap.scrollTop = 0
  })
})

watch(visible, (v) => {
  if (v && props.fileId) loadPreview()
})

async function loadPreview() {
  loading.value = true
  error.value = ''
  // PDF 用 pdf.js 按页渲染（避免 100MB+ 大文件全量加载导致崩溃）
  if (isPdf.value) {
    const token = sessionStorage.getItem('token') || ''
    // 用 blob URL 加载 pdf-viewer HTML，避免被 SPA 路由拦截
    try {
      const resp = await fetch('/pdf-viewer.html?v=2')
      let html = await resp.text()
      // DMS 模式：用 DMS 下载端点；伏羲模式：用 documents/raw 端点
      const pdfEndpoint = isDmsMode.value
        ? `${window.location.origin}/api/dms/download/${props.dmsDocId}?token=${encodeURIComponent(token)}`
        : `${window.location.origin}/api/documents/${props.fileId}/raw?token=${encodeURIComponent(token)}`
      // 注入配置：pdf-viewer 从 window.__PDF_CONFIG 读取
      // 使用 pdfUrl 字段直接指定完整 URL（绕过 fileId 逻辑）
      const configScript = `<script>window.__PDF_CONFIG={pdfUrl:"${pdfEndpoint}",token:"${token}",baseUrl:"${window.location.origin}"}<\/script>`
      html = html.replace('</head>', configScript + '</head>')
      const blob = new Blob([html], { type: 'text/html' })
      pdfUrl.value = URL.createObjectURL(blob)
    } catch (e) {
      // 降级：直接用后端 URL
      if (isDmsMode.value) {
        pdfUrl.value = `/api/dms/download/${props.dmsDocId}?token=${encodeURIComponent(token)}`
      } else {
        pdfUrl.value = `/api/documents/${props.fileId}/raw?token=${encodeURIComponent(token)}`
      }
    }
    loading.value = false
    return
  }
  try {
    // DMS 模式：从 SeedDMS 下载；伏羲模式：从伏羲下载
    let blob
    if (isDmsMode.value) {
      const resp = await api.get(`/dms/download/${props.dmsDocId}`, { responseType: 'blob' })
      blob = resp.data
    } else {
      const resp = await api.get(`/documents/${props.fileId}/raw`, { responseType: 'blob' })
      blob = resp.data
    }
    if (isWord.value) {
      await renderWord(blob)
    } else if (isExcel.value) {
      await renderExcel(blob)
    } else {
      error.value = '该文件格式暂不支持在线预览'
    }
  } catch (e) {
    console.error('预览加载失败', e)
    error.value = '加载失败：' + (e.response?.data?.detail || e.message || '网络错误')
  } finally {
    loading.value = false
  }
}

async function renderWord(blob) {
  const { renderAsync } = await import('docx-preview')
  await nextTick()
  if (wordContainer.value) {
    wordContainer.value.innerHTML = ''
    await renderAsync(blob, wordContainer.value, undefined, { className: 'docx-wrapper' })
  }
}

async function renderExcel(blob) {
  const XLSX = await import('xlsx')
  const buffer = await blob.arrayBuffer()
  const workbook = XLSX.read(buffer, { type: 'array' })
  sheetNames.value = workbook.SheetNames
  activeSheet.value = workbook.SheetNames[0] || ''
  const data = {}
  for (const name of workbook.SheetNames) {
    const sheet = workbook.Sheets[name]
    data[name] = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' })
  }
  sheetsData.value = data
}

function onClose() {
  if (pdfUrl.value && pdfUrl.value.startsWith('blob:')) {
    URL.revokeObjectURL(pdfUrl.value)
  }
  pdfUrl.value = ''
}
</script>

<style scoped>
.preview-loading, .preview-error, .preview-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 0;
  color: var(--text-tertiary);
}
.preview-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* Word 预览容器 */
.preview-word {
  max-height: 72vh;
  overflow-y: auto;
  padding: 20px;
  background: #fff;
  border-radius: 8px;
}
.preview-word :deep(.docx-wrapper) {
  padding: 0;
}
.preview-word :deep(.docx-wrapper > section) {
  box-shadow: none;
  padding: 0;
  margin: 0 auto;
}

/* Excel 预览 */
.preview-excel {
  max-height: 72vh;
  overflow: auto;
}
.excel-table-wrap {
  overflow: auto;
  max-height: 66vh;
  position: relative;
}
.excel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
}
.excel-info {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  font-family: var(--font-mono, monospace);
}
.excel-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
.excel-table td {
  border: 1px solid var(--border, #e4e7ed);
  padding: 5px 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 120px;
}
.excel-header {
  background: var(--bg-subtle, #f5f6f8);
  font-weight: 600;
  position: sticky;
  top: 0;
}

/* PDF 预览 */
.preview-pdf {
  height: 72vh;
}
.preview-pdf iframe {
  width: 100%;
  height: 100%;
  border-radius: 8px;
}
</style>
