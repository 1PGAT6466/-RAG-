<template>
  <div style="height:100%;overflow-y:auto;padding:24px">
    <div v-if="loadError" class="empty-state">
      <el-icon style="font-size:48px"><CircleClose /></el-icon>
      <p>{{ loadError }}</p>
      <el-button size="small" type="primary" @click="loadFile">重试</el-button>
    </div>
    <div v-else-if="!file" class="loading-center"><el-icon class="is-loading" style="font-size:32px"><Loading /></el-icon></div>
    <template v-else>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
        <span style="font-size:28px">{{ iconFor(file.ext) }}</span>
        <div style="flex:1">
          <h2 style="font-size:18px">{{ file.name }}</h2>
          <div style="font-size:12px;color:var(--text-tertiary);margin-top:4px">
            {{ file.chunk_count }} 个分块 · {{ formatSize(file.size) }} · {{ file.category || '未分类' }}
          </div>
        </div>
        <!-- 文档管理操作（仅管理员可见）：删除 + 改分类/标签 -->
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
      <div v-if="chunks.length === 0" class="empty-state"><p>暂无内容</p></div>
      <div v-for="c in chunks" :key="c.id || c.chunk_index"
        :ref="el => setChunkRef(c.chunk_index, el)"
        :class="['chunk-block', { 'chunk-block--highlight': c.chunk_index === highlightChunk }]"
        style="background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;margin-bottom:12px">
        <div style="font-size:12px;color:var(--text-tertiary);margin-bottom:8px">分块 #{{ c.chunk_index + 1 }}</div>
        <div style="line-height:1.8;white-space:pre-wrap;font-size:14px">{{ c.content }}</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loading, CircleClose } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const file = ref(null)
const chunks = ref([])
const highlightChunk = ref(null)
const loadError = ref('')
const chunkEls = {}

function setChunkRef(idx, el) {
  if (el) chunkEls[idx] = el
}

function iconFor(ext) {
  const map = { '.pdf': '📄', '.ppt': '📊', '.pptx': '📊', '.xlsx': '📈', '.xls': '📈', '.docx': '📝', '.doc': '📝' }
  return map[ext?.toLowerCase()] || '📋'
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

async function loadFile() {
  loadError.value = ''
  file.value = null
  chunks.value = []
  try {
    const { data } = await api.get(`/documents/${route.params.id}`)
    file.value = data.data.file
    chunks.value = data.data.chunks || []
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
.chunk-block {
  transition: box-shadow 0.3s, border-color 0.3s;
}
.chunk-block--highlight {
  border-color: var(--el-color-primary, #409eff) !important;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.35);
}
</style>
