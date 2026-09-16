<template>
  <div class="audit-view">
    <PageBack to="/documents" label="返回文档" />
    <div class="page-header">
      <h2>审计日志</h2>
      <p class="subtitle">系统操作记录</p>
    </div>

    <div class="filters">
      <el-select v-model="filterAction" placeholder="操作类型" clearable size="small" @change="loadLogs">
        <el-option label="上传" value="upload" />
        <el-option label="删除" value="delete" />
        <el-option label="永久删除" value="permanent_delete" />
        <el-option label="上传去重" value="upload_dedup" />
      </el-select>
    </div>

    <div v-if="loading" class="loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      加载中...
    </div>

    <div v-else-if="logs.length === 0" class="empty">
      <p>暂无审计记录</p>
    </div>

    <el-table v-else :data="logs" stripe size="small">
      <el-table-column prop="created_at" label="时间" width="160" />
      <el-table-column prop="username" label="用户" width="100" />
      <el-table-column prop="action" label="操作" width="120">
        <template #default="{ row }">
          <el-tag :type="actionType(row.action)" size="small">{{ actionLabel(row.action) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="target_type" label="目标类型" width="100" />
      <el-table-column prop="target_id" label="目标ID" width="80" />
      <el-table-column prop="detail" label="详情" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import PageBack from '../components/PageBack.vue'
import { getAuditLogs } from '@/api/documents'

const loading = ref(true)
const logs = ref([])
const filterAction = ref('')

const loadLogs = async () => {
  loading.value = true
  try {
    const params = { limit: 200 }
    if (filterAction.value) params.action = filterAction.value
    const res = await getAuditLogs(params)
    logs.value = res.data || []
  } catch (e) {
    console.error('加载审计日志失败', e)
    ElMessage.error('加载审计日志失败')
  } finally {
    loading.value = false
  }
}

const actionLabel = (action) => {
  const map = {
    upload: '上传', delete: '删除', permanent_delete: '永久删除',
    upload_dedup: '去重上传', restore: '恢复',
  }
  return map[action] || action
}

const actionType = (action) => {
  if (action === 'delete' || action === 'permanent_delete') return 'danger'
  if (action === 'restore') return 'success'
  return 'info'
}

onMounted(loadLogs)
</script>

<style scoped>
.audit-view {
  padding: 24px;
  max-width: 1100px;
  margin: 0 auto;
  height: 100%;
  overflow-y: auto;
}
.page-header {
  margin-bottom: 16px;
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
.filters {
  margin-bottom: 16px;
  display: flex;
  gap: 12px;
}
.loading, .empty {
  text-align: center;
  padding: 40px 0;
  color: #909399;
}
</style>
