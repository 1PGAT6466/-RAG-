<template>
  <div class="dms-page">
    <PageBack to="/documents" label="返回文档" />
    <div class="page-header">
      <div>
        <h2 class="page-title"><el-icon><Connection /></el-icon>DMS 文档源</h2>
        <p class="page-subtitle">从 SeedDMS 受控导入文档，经伏羲清洗后供检索问答</p>
      </div>
      <div class="header-actions">
        <el-tag :type="connType" size="small">
          {{ connStatus ? '● 已连接' : '● 未连接' }}
        </el-tag>
        <el-button size="small" @click="openConnDialog">
          <el-icon><Setting /></el-icon> 连接设置
        </el-button>
        <el-button size="small" type="primary" plain @click="openDmsWeb">
          <el-icon><TopRight /></el-icon> 打开 DMS 管理页面
        </el-button>
        <el-button size="small" @click="reconnect">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>
    </div>

    <!-- 连接信息 -->
    <div class="conn-bar">
      <span class="conn-label">SeedDMS</span>
      <span class="conn-url">{{ connInfo.url || 'http://localhost:8080' }}</span>
      <span class="conn-user">账号 {{ connInfo.user || 'admin' }}</span>
    </div>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom:16px"
    />

    <div class="dms-layout">
      <!-- 左侧：文件夹树 / 文档列表（分页查阅） -->
      <div class="tree-panel" v-loading="loading">
        <el-tabs v-model="leftTab" class="left-tabs" @tab-change="onLeftTabChange">
          <!-- Tab 1：文件夹树 -->
          <el-tab-pane label="文件夹" name="folder">
            <div class="panel-title">
              <el-icon><Folder /></el-icon> 文件夹
              <el-checkbox
                v-model="selectAllFolder"
                size="small"
                @change="toggleAll"
                style="margin-left:auto"
              >全选</el-checkbox>
            </div>
            <el-tree
              v-if="treeData.length"
              ref="treeRef"
              :data="treeData"
              node-key="treeKey"
              show-checkbox
              :props="treeProps"
              :expand-on-click-node="false"
              @check="onCheckChange"
            >
              <template #default="{ data }">
                <span class="tree-node">
                  <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                  <el-icon v-else><Document /></el-icon>
                  <span class="tree-name">{{ data.name }}</span>
                  <el-tag v-if="data.type === 'document'" :type="docStatusType(data)" size="small" effect="plain">
                    {{ docStatusText(data) }}
                  </el-tag>
                </span>
              </template>
            </el-tree>
            <div v-else class="tree-empty">
              <div class="tree-empty-icon"><el-icon><Folder /></el-icon></div>
              <p class="tree-empty-title">SeedDMS 根目录暂无文档</p>
              <p class="tree-empty-hint">
                请先在 SeedDMS（<span class="mono">{{ connInfo.url || 'http://localhost:8080' }}</span>）
                中上传文档，再回到此处拉取导入。
              </p>
            </div>
          </el-tab-pane>

          <!-- Tab 2：文档列表（分页查阅） -->
          <el-tab-pane :label="`文档列表 (${docListTotal})`" name="list">
            <div class="doc-list-filter">
              <el-radio-group v-model="docStatusFilter" size="small" @change="onDocFilterChange">
                <el-radio-button value="all">全部</el-radio-button>
                <el-radio-button value="unimported">未导入</el-radio-button>
                <el-radio-button value="imported">已导入</el-radio-button>
              </el-radio-group>
            </div>
            <div v-loading="docListLoading" class="doc-list-body">
              <div v-if="docList.length === 0 && !docListLoading" class="tree-empty" style="padding:32px 16px">
                <p class="tree-empty-title">{{ docListError || '暂无文档' }}</p>
                <p v-if="!docListError" class="tree-empty-hint">尝试切换上方「未导入/已导入」筛选，或点击右上角「刷新」。</p>
              </div>
              <div v-for="d in docList" :key="d.id" class="doc-list-item">
                <div class="doc-list-name" :title="d.name">{{ d.name }}</div>
                <div class="doc-list-meta">
                  <span class="doc-list-folder">{{ d.folder_path }}</span>
                  <el-tag :type="d.imported ? 'success' : 'info'" size="small" effect="plain">
                    {{ d.imported ? '已导入' : '未导入' }}
                  </el-tag>
                  <span v-if="d.version" class="doc-list-version">v{{ d.version }}</span>
                </div>
              </div>
            </div>
            <div class="doc-list-pager" v-if="docListTotal > 0">
              <el-pagination
                v-model:current-page="docPage"
                :page-size="docPageSize"
                :total="docListTotal"
                layout="prev, pager, next, total"
                :pager-count="5"
                small
                @current-change="loadDocList"
              />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>

      <!-- 右侧：选中项 + 导入 -->
      <div class="action-panel">
        <div class="panel-title">
          <el-icon><Upload /></el-icon> 导入
        </div>
        <p class="action-hint">
          勾选左侧文档/文件夹后点击导入。已导入且版本未变的文档会自动跳过；有新版本文档会自动替换旧数据。
        </p>

        <div class="selected-summary">
          <div class="summary-row">
            <span>选中文档</span><b>{{ selectedDocCount }}</b>
          </div>
          <div class="summary-row">
            <span>选中文件夹</span><b>{{ selectedFolderCount }}</b>
          </div>
        </div>

        <el-button
          type="primary"
          size="large"
          style="width:100%"
          :disabled="selectedDocCount === 0 && selectedFolderCount === 0"
          :loading="importing"
          @click="doImport"
        >
          <el-icon><Upload /></el-icon> 导入选中
        </el-button>

        <!-- 批量替换：检查更新 -->
        <div class="update-bar">
          <el-button
            size="default"
            style="width:100%"
            :loading="checking"
            @click="doCheckUpdates"
          >
            <el-icon><Refresh /></el-icon> 检查新版本
          </el-button>
          <div v-if="updates && updates.length" class="update-result">
            <div class="update-count">发现 {{ updates.length }} 个新版本文档</div>
            <div v-for="u in updates" :key="u.dms_doc_id" class="update-item">
              <span class="update-name" :title="u.dms_name">{{ u.dms_name }}</span>
            </div>
            <el-button
              type="warning"
              size="small"
              style="width:100%;margin-top:8px"
              :loading="replacing"
              @click="doReplaceAll"
            >
              一键替换全部
            </el-button>
          </div>
          <div v-else-if="updates !== null && updates.length === 0" class="update-empty">
            所有已导入文档均为最新版本
          </div>
        </div>

        <!-- 导入结果 -->
        <div v-if="importResult" class="import-result">
          <el-alert
            :title="`导入完成：${importResult.imported} 新增 / ${importResult.replaced} 替换 / ${importResult.skipped} 跳过`"
            :type="importResult.failed.length ? 'warning' : 'success'"
            :closable="false"
            show-icon
          />
          <div v-if="importResult.failed.length" class="failed-list">
            <div class="failed-title">失败项（{{ importResult.failed.length }}）</div>
            <div v-for="f in importResult.failed" :key="f.doc_id" class="failed-item">
              <span class="failed-name">{{ f.name }}</span>
              <span class="failed-err">{{ f.error }}</span>
            </div>
          </div>
        </div>

        <!-- 已导入记录（追溯） -->
        <div class="records-panel">
          <div class="panel-title">
            <el-icon><Connection /></el-icon> 已导入记录
            <el-button size="small" text @click="loadRecords" style="margin-left:auto">刷新</el-button>
          </div>
          <div v-loading="recordsLoading">
            <div v-if="records.length === 0" class="records-empty">暂无导入记录</div>
            <div v-for="r in records" :key="r.id" class="record-item">
              <span class="record-name" :title="r.dms_name">{{ r.dms_name }}</span>
              <el-tag size="small" :type="r.status === 'replaced' ? 'warning' : 'success'" effect="plain">
                {{ r.status === 'replaced' ? '已替换' : '已导入' }}
              </el-tag>
              <span class="record-meta">v{{ r.dms_version }} · file#{{ r.file_id }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 连接设置弹窗 -->
    <el-dialog v-model="connDialogVisible" title="SeedDMS 连接设置" width="480px">
      <el-form label-width="80px" label-position="left">
        <el-form-item label="服务地址">
          <el-input v-model="connForm.url" placeholder="http://localhost:8080" />
        </el-form-item>
        <el-form-item label="账号">
          <el-input v-model="connForm.user" placeholder="admin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="connForm.password"
            type="password"
            show-password
            :placeholder="connForm.password_set ? '已配置（留空则保持不变）' : '请输入密码'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="connDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingConn" @click="saveConnConfig">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import dmsApi from '../api/dms'
import PageBack from '../components/PageBack.vue'

const loading = ref(false)
const importing = ref(false)
const recordsLoading = ref(false)
const errorMsg = ref('')
const connInfo = ref({})
const treeData = ref([])
const records = ref([])
const importResult = ref(null)
const selectAllFolder = ref(false)
const treeRef = ref(null)

// 左侧 Tab + 文档列表分页
const leftTab = ref('folder')
const docList = ref([])
const docListLoading = ref(false)
const docPage = ref(1)
const docPageSize = 50
const docListTotal = ref(0)
const docStatusFilter = ref('all')
const docListError = ref('')

const connStatus = computed(() => connInfo.value.logged_in === true)
const connType = computed(() => connStatus.value ? 'success' : 'info')

const treeProps = {
  children: 'children',
  label: 'name'
}

// 选中统计
const checkedNodes = ref([])
const selectedDocCount = computed(() => checkedNodes.value.filter(n => n.type === 'document').length)
const selectedFolderCount = computed(() => checkedNodes.value.filter(n => n.type === 'folder').length)

// 连接设置
const connDialogVisible = ref(false)
const savingConn = ref(false)
const connForm = ref({ url: '', user: '', password: '', password_set: false })

// 检查更新
const checking = ref(false)
const replacing = ref(false)
const updates = ref(null)

function docStatusText(data) {
  return data._imported === true ? '已导入' : '未导入'
}
function docStatusType(data) {
  return data._imported === true ? 'success' : 'info'
}

function onCheckChange(data, checked) {
  // 实时刷新选中节点（含半选文件夹的子树）
  refreshChecked()
}

function refreshChecked() {
  checkedNodes.value = treeRef.value?.getCheckedNodes(true) || []
}

function toggleAll(checked) {
  if (checked) {
    treeRef.value?.setCheckedNodes(treeData.value)
  } else {
    treeRef.value?.setCheckedKeys([])
  }
  refreshChecked()
}

async function openConnDialog() {
  try {
    const res = await dmsApi.getConfig()
    const cfg = res.data?.data || {}
    connForm.value = {
      url: cfg.url || '',
      user: cfg.user || '',
      password: '',
      password_set: cfg.password_set === true,
    }
    connDialogVisible.value = true
  } catch (e) {
    ElMessage.error('读取连接配置失败')
  }
}

async function saveConnConfig() {
  savingConn.value = true
  try {
    await dmsApi.updateConfig({
      url: connForm.value.url,
      user: connForm.value.user,
      password: connForm.value.password,
    })
    ElMessage.success('连接配置已保存')
    connDialogVisible.value = false
    // 刷新连接状态
    await reconnect()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    savingConn.value = false
  }
}

async function doCheckUpdates() {
  checking.value = true
  updates.value = null
  try {
    const res = await dmsApi.checkUpdates()
    updates.value = res.data?.data || []
    if (updates.value.length === 0) {
      ElMessage.success('所有已导入文档均为最新版本')
    } else {
      ElMessage.warning(`发现 ${updates.value.length} 个新版本文档`)
    }
  } catch (e) {
    ElMessage.error('检查更新失败：' + (e.response?.data?.detail || e.message))
  } finally {
    checking.value = false
  }
}

async function doReplaceAll() {
  replacing.value = true
  try {
    const res = await dmsApi.replaceAll()
    const data = res.data?.data || {}
    ElMessage.success(`已替换 ${data.updated || 0} 个文档`)
    updates.value = null
    await loadRecords(true)
    markImported(treeData.value)
  } catch (e) {
    ElMessage.error('批量替换失败：' + (e.response?.data?.detail || e.message))
  } finally {
    replacing.value = false
  }
}

let _treeAbortController = null

async function loadTree() {
  if (_treeAbortController) _treeAbortController.abort()
  _treeAbortController = new AbortController()
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await dmsApi.tree()
    const flat = flattenTree(res.data?.data || [])
    // 打导入状态标记（用后端 records 比对）
    treeData.value = res.data?.data || []
    // 加载 records 用于标记状态
    await loadRecords(true)
    markImported(treeData.value)
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '拉取文件夹树失败'
  } finally {
    loading.value = false
  }
}

function flattenTree(nodes, acc = []) {
  for (const n of nodes || []) {
    n.treeKey = `${n.type}-${n.id}`
    acc.push(n)
    if (n.children) flattenTree(n.children, acc)
  }
  return acc
}

function markImported(nodes) {
  const importedSet = new Set(records.value.map(r => r.dms_doc_id))
  for (const n of nodes || []) {
    if (n.type === 'document') {
      n._imported = importedSet.has(n.id)
    }
    if (n.children) markImported(n.children)
  }
}

// 文档列表（分页查阅，每页 50）
let _docListAbortController = null

async function loadDocList() {
  if (_docListAbortController) _docListAbortController.abort()
  _docListAbortController = new AbortController()
  docListLoading.value = true
  docListError.value = ''
  try {
    const res = await dmsApi.documents({
      page: docPage.value,
      page_size: docPageSize,
      status: docStatusFilter.value,
    })
    const data = res.data?.data || {}
    docList.value = data.items || []
    docListTotal.value = data.total || 0
  } catch (e) {
    docList.value = []
    docListTotal.value = 0
    docListError.value = e.response?.data?.detail || '拉取文档列表失败（后端接口可能未更新，请重启服务）'
  } finally {
    docListLoading.value = false
  }
}

function onDocFilterChange() {
  docPage.value = 1
  loadDocList()
}

// 监听左侧 Tab 切换：首次切入文档列表时加载
function onLeftTabChange(name) {
  if (name === 'list') {
    loadDocList()
  }
}

async function loadRecords(silent = false) {
  if (!silent) recordsLoading.value = true
  try {
    const res = await dmsApi.records()
    records.value = res.data?.data || []
  } catch (e) {
    // ignore
  } finally {
    if (!silent) recordsLoading.value = false
  }
}

async function loadHealth() {
  try {
    const res = await dmsApi.health()
    connInfo.value = res.data?.data || {}
  } catch (e) {
    connInfo.value = {}
  }
}

async function reconnect() {
  try {
    await dmsApi.reconnect()
    await loadHealth()
    await loadTree()
    ElMessage.success('已刷新连接')
  } catch (e) {
    ElMessage.error('刷新失败')
  }
}

// 打开 SeedDMS 原生 Web 管理界面（新窗口，需用 SeedDMS 账号登录）
function openDmsWeb() {
  const base = (connInfo.value.url || 'http://localhost:8080').replace(/\/+$/, '')
  if (!base) {
    ElMessage.warning('请先配置 SeedDMS 服务地址')
    return
  }
  // SeedDMS Web UI 登录页（/out/ 目录本身被 Apache 禁止目录列表，必须指向具体文件）
  window.open(base + '/out/out.Login.php', '_blank', 'noopener,noreferrer')
}

async function doImport() {
  const checked = treeRef.value?.getCheckedNodes(true) || []
  const docIds = checked.filter(n => n.type === 'document').map(n => n.id)
  const folderIds = checked.filter(n => n.type === 'folder').map(n => n.id)

  // 若勾选了文件夹，需展开其下所有文档（后端会递归，这里只传 folder id）
  if (docIds.length === 0 && folderIds.length === 0) {
    ElMessage.warning('请先勾选要导入的文档或文件夹')
    return
  }

  importing.value = true
  importResult.value = null
  try {
    const res = await dmsApi.importData(docIds, folderIds)
    importResult.value = res.data?.data || {}
    ElMessage.success(`导入完成：${importResult.value.imported} 新增 / ${importResult.value.replaced} 替换 / ${importResult.value.skipped} 跳过`)
    // 刷新记录和树状态
    await loadRecords(true)
    markImported(treeData.value)
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '导入失败'
    ElMessage.error(errorMsg.value)
  } finally {
    importing.value = false
  }
}

onMounted(async () => {
  await loadHealth()
  await loadTree()
  // 预加载文档列表（不依赖切 tab，进入页面即可见计数与数据）
  loadDocList()
})
</script>

<style scoped>
.dms-page { padding: 20px; height: 100%; overflow-y: auto; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-title { margin: 0; font-size: 18px; font-weight: 700; }
.page-subtitle { margin: 4px 0 0; color: var(--text-tertiary); font-size: 12px; font-family: var(--font-mono); letter-spacing: 0.3px; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.conn-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: var(--bg-subtle, #f5f6f8);
  border-radius: var(--radius, 8px);
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--text-secondary);
}
.conn-label { font-weight: 700; color: var(--accent); }
.conn-url { font-family: monospace; }
.conn-user { margin-left: auto; }

.dms-layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 210px);
  min-height: 480px;
}
.tree-panel {
  flex: 1.2;
  border: 1px solid var(--border);
  border-radius: var(--radius, 8px);
  padding: 12px;
  background: var(--bg-elevated, #fff);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.action-panel {
  flex: 1;
  border: 1px solid var(--border);
  border-radius: var(--radius, 8px);
  padding: 12px;
  background: var(--bg-elevated, #fff);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 10px;
}
.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.tree-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 16px;
  text-align: center;
  color: var(--text-tertiary);
}
.tree-empty-icon {
  font-size: 40px;
  opacity: 0.35;
  margin-bottom: 12px;
}
.tree-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0 0 6px;
}
.tree-empty-hint {
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
  max-width: 280px;
}
.tree-empty-hint .mono { font-family: var(--font-mono, monospace); }
.tree-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.left-tabs { margin-top: -6px; flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.left-tabs :deep(.el-tabs__header) { margin-bottom: 10px; flex-shrink: 0; }
.left-tabs :deep(.el-tabs__item) { font-size: 13px; }
.left-tabs :deep(.el-tabs__content) { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.left-tabs :deep(.el-tab-pane) { overflow-y: auto; flex: 1; }

.doc-list-filter { margin-bottom: 10px; }
.doc-list-body {
  min-height: 280px;
  max-height: 520px;
  overflow-y: auto;
}
.doc-list-item {
  padding: 8px 10px;
  border-bottom: 1px dashed var(--border);
}
.doc-list-item:hover { background: var(--bg-subtle, #f5f6f8); }
.doc-list-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-list-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}
.doc-list-folder {
  font-family: var(--font-mono, monospace);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}
.doc-list-version { font-family: var(--font-mono, monospace); }
.doc-list-pager {
  display: flex;
  justify-content: center;
  padding: 10px 0 2px;
}
.action-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
  margin-bottom: 12px;
}
.selected-summary {
  border: 1px dashed var(--border);
  border-radius: var(--radius, 8px);
  padding: 10px;
  margin-bottom: 12px;
}
.summary-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  padding: 4px 0;
}
.summary-row b { color: var(--accent); }

.import-result { margin-top: 16px; }
.failed-list { margin-top: 10px; font-size: 12px; }
.failed-title { font-weight: 700; color: var(--text-secondary); margin-bottom: 6px; }
.failed-item {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  background: var(--bg-subtle, #f5f6f8);
  border-radius: 6px;
  margin-bottom: 4px;
}
.failed-name { font-weight: 500; }
.failed-err { color: var(--text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.records-panel {
  margin-top: 20px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}
.records-panel > div[v-loading] {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.records-empty { font-size: 12px; color: var(--text-tertiary); text-align: center; padding: 12px 0; }
.record-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 12px;
  border-bottom: 1px dashed var(--border);
}
.record-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.record-meta { color: var(--text-tertiary); font-size: 11px; }

.update-bar { margin-top: 16px; }
.update-result { margin-top: 8px; padding: 10px; background: var(--bg-subtle, #f5f6f8); border-radius: 8px; }
.update-count { font-size: 13px; font-weight: 700; color: var(--accent); margin-bottom: 6px; }
.update-item { font-size: 12px; padding: 4px 0; border-bottom: 1px dashed var(--border); }
.update-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
.update-empty { font-size: 12px; color: var(--text-tertiary); text-align: center; padding: 8px 0; }
</style>
