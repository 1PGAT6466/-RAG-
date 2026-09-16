<template>
  <div class="config-view">
    <PageBack to="/" label="返回对话" />
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title"><el-icon><Setting /></el-icon>系统配置</h2>
        <p class="page-subtitle">运行参量 · 功能开关 · 修改后重启服务生效</p>
      </div>
      <div class="page-header-actions">
        <el-button size="small" :icon="Refresh" @click="load" :loading="loading">刷新</el-button>
      </div>
    </div>

    <LoadingBlock v-if="loading" text="加载中..." />

    <EmptyState v-else-if="groupKeys.length === 0" title="无可管理配置" />

    <div v-else class="config-groups">
      <div v-for="g in groupKeys" :key="g" class="config-group">
        <div class="group-label">{{ g }}</div>
        <div class="config-item" v-for="item in groups[g]" :key="item.key">
          <div class="item-info">
            <div class="item-label">
              {{ item.label }}
              <el-tag v-if="item.pending_restart" size="small" type="warning" style="margin-left:8px">待重启生效</el-tag>
            </div>
            <div class="item-desc">{{ item.desc }}</div>
          </div>
          <div class="item-control">
            <!-- 布尔开关 -->
            <el-switch
              v-if="item.type === 'bool'"
              :model-value="item.value === '1'"
              @change="(val) => save(item, val ? '1' : '0')"
              :disabled="savingKey === item.key"
            />
            <!-- 数字输入 -->
            <el-input-number
              v-else-if="item.type === 'int' || item.type === 'float'"
              :model-value="Number(item.value)"
              :precision="item.type === 'float' ? 3 : 0"
              :step="item.type === 'float' ? 0.05 : 1"
              :min="item.type === 'float' ? 0 : 0"
              size="small"
              controls-position="right"
              @change="(val) => save(item, String(val))"
              :disabled="savingKey === item.key"
            />
            <!-- 字符串 -->
            <el-input
              v-else
              :model-value="item.value"
              size="small"
              style="width:160px"
              @change="(val) => save(item, val)"
              :disabled="savingKey === item.key"
            />
          </div>
        </div>
      </div>
    </div>

    <div class="config-footer" style="margin-top:20px">
      <div class="group-label" style="margin-bottom:8px">系统诊断</div>
      <div class="diag-grid">
        <div class="diag-card">
          <div class="diag-title">依赖健康</div>
          <div class="diag-row"><span>数据库</span><b>{{ diag.db || '--' }}</b></div>
          <div class="diag-row"><span>向量库</span><b>{{ diag.chroma || '--' }}</b></div>
          <div class="diag-row"><span>LLM 配置</span><b>{{ diag.llm_configured === undefined ? '--' : (diag.llm_configured ? '已配置' : '未配置') }}</b></div>
          <div class="diag-row"><span>磁盘剩余</span><b>{{ diag.disk_free_gb ? diag.disk_free_gb + ' GB' : '--' }}</b></div>
        </div>
        <div class="diag-card">
          <div class="diag-title">LLM 调用审计</div>
          <div class="diag-row"><span>总调用</span><b>{{ llmTotalCalls }}</b></div>
          <div class="diag-row"><span>输入 token</span><b>{{ diag.llm_total_tokens ? diag.llm_total_tokens.in : '--' }}</b></div>
          <div class="diag-row"><span>输出 token</span><b>{{ diag.llm_total_tokens ? diag.llm_total_tokens.out : '--' }}</b></div>
          <div class="diag-row"><span>降级率</span><b>{{ degradationText }}</b></div>
        </div>
        <div class="diag-card">
          <div class="diag-title">最近检索耗时</div>
          <div v-if="searchProfile" class="diag-row"><span>总耗时</span><b>{{ searchProfile.total_ms }} ms</b></div>
          <div v-if="searchProfile" class="diag-row"><span>查询</span><b style="font-size:11px">{{ searchProfile.query }}</b></div>
          <div v-else class="diag-row"><span>提示</span><b>暂无检索记录</b></div>
          <div v-if="searchProfile && searchProfile.stages" class="diag-stages">
            <div v-for="(ms, name) in searchProfile.stages" :key="name" class="diag-row">
              <span>{{ name }}</span><b>{{ ms }}</b>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="config-footer">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="说明"
        description="这里只展示可在线调整的运行参数与功能开关。密钥、路径、端口等部署配置不在此暴露。所有修改写入 .env，需重启服务后生效。"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, Setting } from '@element-plus/icons-vue'
import PageBack from '../components/PageBack.vue'
// ElMessage 由 unplugin-auto-import 自动引入（含样式）
import EmptyState from '../components/EmptyState.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import configApi from '../api/config'

const loading = ref(false)
const groups = ref({})
const groupKeys = ref([])
const savingKey = ref('')
const diag = ref({})

const searchProfile = computed(() => diag.value.last_search_profile_ms || null)
const llmTotalCalls = computed(() => {
  const calls = diag.value.llm_calls || {}
  return Object.values(calls).reduce((a, b) => a + b, 0)
})
const degradationText = computed(() => {
  const r = diag.value.llm_degradation_rate || {}
  const entries = Object.entries(r)
  if (entries.length === 0) return '--'
  return entries.map(([k, v]) => `${k}: ${(v * 100).toFixed(1)}%`).join(' ')
})

async function load() {
  loading.value = true
  try {
    const res = await configApi.list()
    groups.value = res.data.groups || {}
    groupKeys.value = Object.keys(groups.value)
  } catch (e) {
    ElMessage.error('加载配置失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadDiag() {
  try {
    const res = await configApi.health()
    diag.value = res.data.checks || {}
  } catch (e) {
    // 诊断加载失败不阻断主流程
  }
}

async function save(item, value) {
  savingKey.value = item.key
  try {
    await configApi.update(item.key, value)
    item.value = value
    ElMessage.success(`已更新 ${item.label} = ${value}（重启后生效）`)
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    savingKey.value = ''
  }
}

onMounted(() => {
  load()
  loadDiag()
})
</script>

<style scoped>
.config-view {
  padding-bottom: 24px;
  height: 100%;
  overflow-y: auto;
}
.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.config-groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 20px 24px 0;
  max-width: 920px;
}
.config-group {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
}
.group-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.config-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.config-item:last-child {
  border-bottom: none;
}
.item-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.item-desc {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
.item-control {
  flex-shrink: 0;
  margin-left: 16px;
}
.config-footer {
  margin-top: 20px;
  padding: 0 24px;
  max-width: 920px;
}
.diag-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
}
.diag-card {
  background: var(--bg-elevated, #fff);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.diag-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.diag-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--text-secondary);
  padding: 4px 0;
}
.diag-row b {
  color: var(--text-primary);
  font-weight: 600;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.diag-stages {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border);
}
.empty-state {
  text-align: center;
  color: var(--text-tertiary);
  padding: 60px 0;
}
</style>
