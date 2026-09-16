<template>
  <div class="recycle-bin">
    <PageBack to="/documents" label="返回文档" />
    <div class="page-header">
      <h2>回收站</h2>
      <p class="subtitle">已删除的文件（30 天后自动清除）</p>
    </div>

    <div v-if="loading" class="loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      加载中...
    </div>

    <div v-else-if="files.length === 0" class="empty">
      <el-icon :size="48" color="#c0c4cc"><Delete /></el-icon>
      <p>回收站为空</p>
    </div>

    <div v-else class="file-list">
      <div v-for="file in files" :key="file.id" class="file-item">
        <div class="file-info">
          <el-icon class="file-icon"><Document /></el-icon>
          <div class="file-meta">
            <span class="file-name">{{ file.name }}</span>
            <span class="file-detail">
              {{ file.ext }} · {{ formatSize(file.size) }} ·
              删除于 {{ formatTime(file.deleted_at) }}
            </span>
          </div>
        </div>
        <div class="file-actions">
          <el-button size="small" type="primary" @click="restore(file)">
            <el-icon><RefreshLeft /></el-icon>
            恢复
          </el-button>
          <el-button size="small" type="danger" @click="permanentDelete(file)">
            <el-icon><Delete /></el-icon>
            永久删除
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, RefreshLeft, Loading } from '@element-plus/icons-vue'
import PageBack from '../components/PageBack.vue'
import { recycleBin, restoreFile, permanentDeleteFile } from '@/api/documents'
import { formatSize } from './docs/helpers'
import { formatTime } from './chat/helpers'

const loading = ref(true)
const files = ref([])

const loadFiles = async () => {
  loading.value = true
  try {
    const res = await recycleBin()
    files.value = res.data?.data || []
  } catch (e) {
    ElMessage.error('加载回收站失败')
  } finally {
    loading.value = false
  }
}

const restore = async (file) => {
  try {
    await restoreFile(file.id)
    ElMessage.success(`已恢复: ${file.name}`)
    loadFiles()
  } catch (e) {
    ElMessage.error('恢复失败')
  }
}

const permanentDelete = async (file) => {
  try {
    await ElMessageBox.confirm(
      `确定永久删除 "${file.name}"？此操作不可撤销。`,
      '永久删除',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await permanentDeleteFile(file.id)
    ElMessage.success(`已永久删除: ${file.name}`)
    loadFiles()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

onMounted(loadFiles)
</script>

<style scoped>
.recycle-bin {
  padding: 24px;
  max-width: 900px;
  margin: 0 auto;
  height: 100%;
  overflow-y: auto;
}
.page-header {
  margin-bottom: 24px;
}
.page-header h2 {
  margin: 0 0 4px 0;
  font-size: 20px;
  color: #303133;
}
.subtitle {
  margin: 0;
  color: #909399;
  font-size: 13px;
}
.loading, .empty {
  text-align: center;
  padding: 60px 0;
  color: #909399;
}
.empty p {
  margin-top: 12px;
}
.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  transition: border-color 0.2s;
}
.file-item:hover {
  border-color: #409eff;
}
.file-info {
  display: flex;
  align-items: center;
  gap: 12px;
}
.file-icon {
  font-size: 24px;
  color: #909399;
}
.file-meta {
  display: flex;
  flex-direction: column;
}
.file-name {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}
.file-detail {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.file-actions {
  display: flex;
  gap: 8px;
}
</style>
