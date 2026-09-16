<template>
  <div class="plugins-view">
    <PageBack to="/" label="返回对话" />
    <!-- 顶部标题栏 -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title"><el-icon><Grid /></el-icon>插件管理</h2>
        <p class="page-subtitle">已加载 {{ plugins.length }} 个插件 · 启用 {{ enabledPlugins.length }} 个</p>
      </div>
      <div class="page-header-actions">
        <el-button size="small" :icon="Refresh" @click="refresh" :loading="loading">刷新</el-button>
      </div>
    </div>

    <div class="plugins-body">
      <!-- 左侧插件列表 -->
      <div class="plugins-list">
        <EmptyState v-if="plugins.length === 0" icon="Grid" title="暂无插件" hint="在 plugins/ 目录下放置 manifest.json 即可加载" />
        <div
          v-for="p in plugins"
          :key="p.name"
          class="plugin-card"
          :class="{ active: current?.name === p.name }"
          @click="select(p)"
        >
          <div class="plugin-card-head">
            <span class="plugin-name">{{ p.manifest?.display_name || p.name }}</span>
            <el-tag :type="p.status === 'enabled' ? 'success' : 'info'" size="small">
              {{ p.status === 'enabled' ? '已启用' : p.status === 'disabled' ? '已停用' : '已安装' }}
            </el-tag>
          </div>
          <div class="plugin-card-meta">
            <span>{{ p.name }}</span>
            <span>v{{ p.manifest?.version }}</span>
            <el-tag size="small" effect="plain">{{ p.kind === 'tool' ? '工具' : '工作流' }}</el-tag>
          </div>
          <div class="plugin-card-desc">{{ p.manifest?.description }}</div>
        </div>
      </div>

      <!-- 右侧详情 -->
      <div class="plugins-detail" v-if="current">
        <h3 class="plugin-detail-title">{{ current.manifest?.display_name || current.name }}</h3>

        <!-- 操作按钮 -->
        <div style="display:flex;gap:8px;margin-bottom:20px">
          <el-button v-if="current.status !== 'enabled'" type="primary" size="small" @click="enable">启用</el-button>
          <el-button v-else type="warning" size="small" @click="disable">停用</el-button>
          <el-button type="danger" size="small" @click="uninstall">卸载</el-button>
        </div>

        <!-- 基本信息 -->
        <el-descriptions :column="1" size="small" border style="margin-bottom:20px">
          <el-descriptions-item label="标识">{{ current.name }}</el-descriptions-item>
          <el-descriptions-item label="版本">v{{ current.manifest?.version }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ current.kind === 'tool' ? 'Tool 工具' : 'Workflow 工作流' }}</el-descriptions-item>
          <el-descriptions-item label="作者">{{ current.manifest?.author || '—' }}</el-descriptions-item>
          <el-descriptions-item label="描述">{{ current.manifest?.description || '—' }}</el-descriptions-item>
        </el-descriptions>

        <!-- Tool 型：工具调用面板 -->
        <template v-if="current.kind === 'tool' && current.manifest?.tools?.length">
          <div class="section-title">工具调用</div>
          <el-select
            v-model="selectedTool"
            placeholder="选择工具"
            style="width:100%;margin-bottom:12px"
            @change="onToolChange"
          >
            <el-option
              v-for="t in current.manifest.tools"
              :key="t.name"
              :label="`${t.name} — ${t.description || ''}`"
              :value="t.name"
            />
          </el-select>

          <template v-if="selectedToolSchema">
            <SchemaForm v-model="toolParams" :schema="selectedToolSchema" />
            <el-button type="primary" size="small" @click="invokeTool" :loading="invoking" style="margin-top:12px">
              执行
            </el-button>
            <div v-if="invokeResult" style="margin-top:16px">
              <div class="section-title">执行结果</div>
              <SchemaResult :result="invokeResult" />
            </div>
          </template>
        </template>

        <!-- Workflow 型提示 -->
        <template v-else-if="current.kind === 'workflow'">
          <EmptyState title="Workflow 插件" hint="通过挂载到入库/检索节点自动执行" />
        </template>
      </div>

      <!-- 右侧空态 -->
      <div class="plugins-detail" v-else>
        <EmptyState title="选择一个插件查看详情" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, Grid } from '@element-plus/icons-vue'
import PageBack from '../components/PageBack.vue'
import EmptyState from '../components/EmptyState.vue'
// ElMessage/ElMessageBox 由 unplugin-auto-import 自动引入（含样式）
import { usePluginsStore } from '../stores/plugins'
import SchemaForm from '../components/SchemaForm.vue'
import SchemaResult from '../components/SchemaResult.vue'

const store = usePluginsStore()
const plugins = computed(() => store.plugins)
const enabledPlugins = computed(() => store.enabledPlugins)
const loading = computed(() => store.loading)

const current = ref(null)
const selectedTool = ref('')
const toolParams = ref({})
const invokeResult = ref(null)
const invoking = ref(false)

const selectedToolSchema = computed(() => {
  if (!current.value || !selectedTool.value) return null
  const tool = current.value.manifest?.tools?.find(t => t.name === selectedTool.value)
  return tool?.parameters_schema || null
})

onMounted(() => store.fetchList())

function refresh() {
  store.fetchList()
}

function select(p) {
  current.value = p
  selectedTool.value = ''
  toolParams.value = {}
  invokeResult.value = null
  // 默认选中第一个工具
  if (p.kind === 'tool' && p.manifest?.tools?.length) {
    selectedTool.value = p.manifest.tools[0].name
  }
}

function onToolChange() {
  toolParams.value = {}
  invokeResult.value = null
}

async function enable() {
  try {
    await store.enable(current.value.name)
    ElMessage.success('已启用')
    current.value = store.plugins.find(p => p.name === current.value.name) || current.value
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启用失败')
  }
}

async function disable() {
  try {
    await store.disable(current.value.name)
    ElMessage.success('已停用')
    current.value = store.plugins.find(p => p.name === current.value.name) || current.value
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '停用失败')
  }
}

async function uninstall() {
  try {
    await ElMessageBox.confirm(`确认卸载插件 ${current.value.name}？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.uninstall(current.value.name)
    ElMessage.success('已卸载')
    current.value = null
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '卸载失败')
  }
}

async function invokeTool() {
  if (!current.value || !selectedTool.value) return
  invoking.value = true
  invokeResult.value = null
  try {
    invokeResult.value = await store.invoke(current.value.name, selectedTool.value, toolParams.value)
  } catch (e) {
    invokeResult.value = { status: 'error', data: { error: e.response?.data?.detail || e.message } }
  } finally {
    invoking.value = false
  }
}
</script>

<style scoped>
.plugins-view { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.plugins-body { flex: 1; display: flex; overflow: hidden; }
.plugins-list {
  width: 320px;
  min-width: 320px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 12px;
  background: var(--bg-secondary);
}
.plugin-card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: all .15s;
}
.plugin-card:hover { border-color: var(--accent); }
.plugin-card.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-light); }
.plugin-card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.plugin-name { font-weight: 600; }
.plugin-card-meta { display: flex; gap: 8px; align-items: center; font-size: 12px; color: var(--text-tertiary); margin-bottom: 6px; }
.plugin-card-desc { font-size: 12px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plugins-detail { flex: 1; overflow-y: auto; padding: 20px; background: var(--bg-primary); }
.plugin-detail-title {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 16px;
  color: var(--text-primary);
}
.section-title { font-weight: 600; margin-bottom: 10px; color: var(--text-primary); }
.empty-state { text-align: center; color: var(--text-tertiary); padding: 40px 0; }
</style>
