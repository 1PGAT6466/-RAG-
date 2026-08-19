<template>
  <div class="mcp-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">MCP 市场</h2>
        <p class="page-sub">浏览并安装标准化 MCP server，扩展知识库能力</p>
      </div>
      <el-tag v-if="!auth.isAdmin" type="info" size="small">仅管理员可安装/卸载</el-tag>
    </div>

    <!-- Tab：市场 / 已安装 -->
    <div class="mcp-tabs">
      <el-radio-group v-model="tab" size="large">
        <el-radio-button value="market">市场</el-radio-button>
        <el-radio-button value="installed">已安装 ({{ installed.length }})</el-radio-button>
      </el-radio-group>
    </div>

    <!-- ============ 市场浏览 ============ -->
    <div v-if="tab === 'market'">
      <div class="market-search">
        <el-input
          v-model="keyword"
          placeholder="搜索 MCP server（名称/描述）"
          clearable
          size="large"
          @keyup.enter="loadMarket"
          @clear="loadMarket"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
          <template #append>
            <el-button @click="loadMarket">搜索</el-button>
          </template>
        </el-input>
      </div>

      <el-alert
        v-if="marketError"
        :title="marketError"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom:16px"
      />

      <div v-loading="marketLoading" class="market-grid">
        <div v-for="s in market" :key="s.qualifiedName" class="market-card">
          <div class="card-head">
            <div class="card-icon">{{ (s.displayName || s.qualifiedName || '?')[0] }}</div>
            <div class="card-title-wrap">
              <div class="card-title">
                {{ s.displayName || s.qualifiedName }}
                <el-tag v-if="s.verified" type="success" size="small">官方</el-tag>
              </div>
              <div class="card-qname">{{ s.qualifiedName }}</div>
            </div>
          </div>
          <p class="card-desc">{{ s.description || '暂无描述' }}</p>
          <div class="card-meta">
            <span v-if="s.useCount"><el-icon><User /></el-icon> {{ s.useCount }}</span>
            <span v-if="s.homepage">{{ shortUrl(s.homepage) }}</span>
          </div>
          <div class="card-actions">
            <el-button
              v-if="auth.isAdmin"
              type="primary"
              size="small"
              :disabled="isInstalled(s.qualifiedName)"
              @click="openInstall(s)"
            >
              {{ isInstalled(s.qualifiedName) ? '已安装' : '安装' }}
            </el-button>
            <el-button v-if="s.homepage" size="small" @click="openUrl(s.homepage)">主页</el-button>
          </div>
        </div>
      </div>

      <el-empty v-if="!marketLoading && market.length === 0" description="未找到 MCP server（可能是 registry 暂时不可用）" />
    </div>

    <!-- ============ 已安装 ============ -->
    <div v-else>
      <div v-loading="installedLoading">
        <div v-for="s in installed" :key="s.qualifiedName" class="installed-card">
          <div class="installed-head">
            <div>
              <div class="installed-name">
                {{ s.displayName || s.qualifiedName }}
                <el-tag size="small" type="info">{{ s.qualifiedName }}</el-tag>
              </div>
              <div class="installed-cmd">{{ s.command }} {{ (s.args || []).join(' ') }}</div>
            </div>
            <div class="installed-actions">
              <el-button size="small" @click="toggleTools(s)">工具 ({{ getToolCount(s) }})</el-button>
              <el-button v-if="auth.isAdmin" size="small" type="danger" plain @click="doUninstall(s)">卸载</el-button>
            </div>
          </div>

          <!-- 工具列表 + 调用 -->
          <div v-if="expandedQn === s.qualifiedName" v-loading="toolsLoading" class="tools-panel">
            <div v-if="toolsError" class="tools-error">{{ toolsError }}</div>
            <template v-else>
              <div v-for="t in currentTools" :key="t.name" class="tool-item">
                <div class="tool-head">
                  <span class="tool-name">{{ t.name }}</span>
                  <el-button size="small" type="primary" plain @click="openCall(s, t)">调用</el-button>
                </div>
                <p class="tool-desc">{{ t.description || '—' }}</p>
              </div>
              <el-empty v-if="currentTools.length === 0" description="该 server 无可用工具" :image-size="60" />
            </template>
          </div>
        </div>
      </div>
      <el-empty v-if="!installedLoading && installed.length === 0" description="尚未安装任何 MCP server，去市场逛逛吧" />
    </div>

    <!-- ============ 安装弹窗 ============ -->
    <el-dialog v-model="installVisible" title="安装 MCP Server" width="520px">
      <el-form label-width="110px">
        <el-form-item label="名称">
          <el-input :model-value="installTarget?.displayName || installTarget?.qualifiedName" disabled />
        </el-form-item>
        <el-form-item label="完整标识">
          <el-input v-model="installForm.qualifiedName" placeholder="如 @smithery-ai/server-sequential-thinking" />
        </el-form-item>
        <el-form-item label="启动命令" required>
          <el-input v-model="installForm.command" placeholder="如 npx / node / python" />
        </el-form-item>
        <el-form-item label="命令参数">
          <el-input v-model="installForm.argsText" placeholder="空格分隔，如 -y @modelcontextprotocol/server-filesystem" />
        </el-form-item>
        <el-form-item label="环境变量">
          <el-input v-model="installForm.envText" type="textarea" :rows="2" placeholder="KEY=VALUE，每行一个" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="installVisible = false">取消</el-button>
        <el-button type="primary" :loading="installing" @click="doInstall">安装</el-button>
      </template>
    </el-dialog>

    <!-- ============ 工具调用弹窗 ============ -->
    <el-dialog v-model="callVisible" :title="`调用工具：${callTarget?.tool}`" width="560px">
      <div v-if="callTarget?.tool">
        <p class="call-desc">{{ callTarget.tool.description || '' }}</p>
        <el-form label-position="top">
          <el-form-item v-for="(v, k) in callParams" :key="k" :label="k">
            <el-input v-model="callParams[k]" :placeholder="describeField(callTarget.tool, k)" />
          </el-form-item>
        </el-form>
      </div>
      <div v-if="callParamsEmpty" class="tools-error">该工具无入参，可直接调用。</div>
      <div v-if="callResult" class="call-result">
        <div class="call-result-title">结果</div>
        <pre class="call-result-body">{{ callResult }}</pre>
      </div>
      <template #footer>
        <el-button @click="callVisible = false">关闭</el-button>
        <el-button type="primary" :loading="calling" @click="doCall">调用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import mcpApi from '../api/mcp'

const auth = useAuthStore()
const tab = ref('market')

// 市场
const keyword = ref('')
const market = ref([])
const marketLoading = ref(false)
const marketError = ref('')

// 已安装
const installed = ref([])
const installedLoading = ref(false)
const expandedQn = ref('')
const currentTools = ref([])
const toolsLoading = ref(false)
const toolsError = ref('')

// 安装
const installVisible = ref(false)
const installing = ref(false)
const installTarget = ref(null)
const installForm = ref({ qualifiedName: '', command: '', argsText: '', envText: '' })

// 调用
const callVisible = ref(false)
const calling = ref(false)
const callTarget = ref(null) // { server, tool }
const callParams = ref({})
const callResult = ref('')

const callParamsEmpty = computed(() => Object.keys(callParams.value).length === 0)

function isInstalled(qn) {
  return installed.value.some(s => s.qualifiedName === qn)
}

function shortUrl(u) {
  try {
    const x = new URL(u)
    return x.hostname
  } catch {
    return u
  }
}

function openUrl(u) {
  window.open(u, '_blank')
}

async function loadMarket() {
  marketLoading.value = true
  marketError.value = ''
  try {
    const { data } = await mcpApi.market(keyword.value)
    market.value = data.data || []
  } catch (e) {
    marketError.value = '市场加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    marketLoading.value = false
  }
}

async function loadInstalled() {
  installedLoading.value = true
  try {
    const { data } = await mcpApi.installed()
    installed.value = data.data || []
  } catch (e) {
    console.error('加载已安装 MCP 失败', e)
  } finally {
    installedLoading.value = false
  }
}

function openInstall(s) {
  installTarget.value = s
  installForm.value = {
    qualifiedName: s.qualifiedName,
    command: '',
    argsText: '',
    envText: ''
  }
  installVisible.value = true
}

async function doInstall() {
  if (!installForm.value.command.trim()) {
    return
  }
  installing.value = true
  try {
    const args = installForm.value.argsText.trim()
      ? installForm.value.argsText.trim().split(/\s+/)
      : []
    const env = {}
    if (installForm.value.envText.trim()) {
      for (const line of installForm.value.envText.trim().split('\n')) {
        const idx = line.indexOf('=')
        if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
      }
    }
    await mcpApi.install({
      qualifiedName: installForm.value.qualifiedName,
      command: installForm.value.command.trim(),
      args,
      env
    })
    installVisible.value = false
    await loadInstalled()
    tab.value = 'installed'
  } catch (e) {
    console.error('安装失败', e)
    this.$message?.error?.('安装失败')
  } finally {
    installing.value = false
  }
}

async function doUninstall(s) {
  try {
    await mcpApi.uninstall(s.qualifiedName)
    if (expandedQn.value === s.qualifiedName) expandedQn.value = ''
    await loadInstalled()
  } catch (e) {
    console.error('卸载失败', e)
  }
}

function getToolCount(s) {
  return s._toolCount !== undefined ? s._toolCount : '?'
}

async function toggleTools(s) {
  if (expandedQn.value === s.qualifiedName) {
    expandedQn.value = ''
    return
  }
  expandedQn.value = s.qualifiedName
  currentTools.value = []
  toolsError.value = ''
  toolsLoading.value = true
  try {
    const { data } = await mcpApi.tools(s.qualifiedName)
    currentTools.value = data.data || []
    s._toolCount = currentTools.value.length
  } catch (e) {
    toolsError.value = '无法连接 MCP server：' + (e.response?.data?.detail || e.message)
  } finally {
    toolsLoading.value = false
  }
}

function describeField(tool, key) {
  const schema = tool?.inputSchema
  const props = schema?.properties || {}
  const p = props[key]
  return p?.description || (p?.type ? p.type : '')
}

function openCall(server, tool) {
  callTarget.value = { server, tool }
  callResult.value = ''
  const props = tool?.inputSchema?.properties || {}
  const required = tool?.inputSchema?.required || []
  callParams.value = {}
  for (const k of Object.keys(props)) {
    callParams.value[k] = ''
  }
  // 无参工具直接可调用
  callVisible.value = true
}

async function doCall() {
  if (!callTarget.value) return
  calling.value = true
  callResult.value = ''
  try {
    // 解析入参（尝试 JSON，否则字符串）
    const args = {}
    for (const [k, v] of Object.entries(callParams.value)) {
      if (v === '' || v == null) continue
      try {
        args[k] = JSON.parse(v)
      } catch {
        args[k] = v
      }
    }
    const { data } = await mcpApi.call(callTarget.value.server.qualifiedName, callTarget.value.tool.name, args)
    const d = data.data || {}
    callResult.value = d.content || d.error || JSON.stringify(d, null, 2)
  } catch (e) {
    callResult.value = '调用失败：' + (e.response?.data?.detail || e.message)
  } finally {
    calling.value = false
  }
}

onMounted(() => {
  loadMarket()
  loadInstalled()
})
</script>

<style scoped>
.mcp-page {
  padding: 24px 28px;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
}
.page-sub {
  margin: 0;
  font-size: 13px;
  color: var(--text-tertiary, #999);
}
.mcp-tabs {
  margin-bottom: 20px;
}
.market-search {
  margin-bottom: 16px;
  max-width: 520px;
}
.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.market-card,
.installed-card {
  background: var(--bg-elevated, #fff);
  border: 1px solid var(--border, #e5e5e5);
  border-radius: var(--radius, 10px);
  padding: 16px;
  transition: box-shadow 0.2s, transform 0.2s;
}
.market-card:hover {
  box-shadow: var(--shadow, 0 4px 16px rgba(0,0,0,0.08));
  transform: translateY(-2px);
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.card-icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: var(--accent-light, #eef2ff);
  color: var(--accent, #4f46e5);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  flex-shrink: 0;
}
.card-title-wrap {
  min-width: 0;
}
.card-title {
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.card-qname {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-desc {
  font-size: 13px;
  color: var(--text-secondary, #666);
  min-height: 40px;
  margin: 0 0 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-meta {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 12px;
}
.card-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.card-actions {
  display: flex;
  gap: 8px;
}

.installed-card {
  margin-bottom: 12px;
}
.installed-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.installed-name {
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.installed-cmd {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  font-family: monospace;
  margin-top: 4px;
}
.installed-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.tools-panel {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--border, #e5e5e5);
}
.tool-item {
  padding: 10px 12px;
  border: 1px solid var(--border, #e5e5e5);
  border-radius: 8px;
  margin-bottom: 8px;
}
.tool-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tool-name {
  font-weight: 600;
  font-family: monospace;
  font-size: 13px;
  color: var(--accent, #4f46e5);
}
.tool-desc {
  font-size: 13px;
  color: var(--text-secondary, #666);
  margin: 6px 0 0;
}
.tools-error {
  font-size: 13px;
  color: var(--el-color-danger, #f56c6c);
  padding: 8px 0;
}
.call-desc {
  font-size: 13px;
  color: var(--text-secondary, #666);
  margin: 0 0 8px;
}
.call-result {
  margin-top: 8px;
}
.call-result-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.call-result-body {
  background: var(--bg-subtle, #f5f6f8);
  border: 1px solid var(--border, #e5e5e5);
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
