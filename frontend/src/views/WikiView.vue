<template>
  <div class="wiki-view">
    <!-- 顶部：页面列表 -->
    <div class="wiki-sidebar">
      <div class="wiki-sidebar-header">
        <h3>📚 Wiki 知识页</h3>
        <el-button v-if="auth.isAdmin" size="small" type="primary" @click="showCreate = true">
          <el-icon><Plus /></el-icon> 新建
        </el-button>
      </div>
      <div class="wiki-list">
        <div
          v-for="page in pages" :key="page.id"
          class="wiki-item"
          :class="{ active: currentPage?.id === page.id }"
          @click="loadPage(page.id)"
        >
          <div class="wiki-item-title">{{ page.title }}</div>
          <div class="wiki-item-meta">
            <el-tag size="small" :type="statusType(page.status)">{{ page.status }}</el-tag>
            <span class="wiki-item-cat">{{ page.category }}</span>
          </div>
        </div>
        <div v-if="pages.length === 0" class="wiki-empty">暂无知识页</div>
      </div>
    </div>

    <!-- 右侧：页面详情 -->
    <div class="wiki-content">
      <div v-if="!currentPage" class="wiki-placeholder">
        <p>← 选择一个知识页查看</p>
      </div>
      <div v-else>
        <div class="wiki-content-header">
          <h2>{{ currentPage.title }}</h2>
          <div class="wiki-content-actions">
            <el-button v-if="auth.isAdmin" size="small" @click="startEdit">编辑</el-button>
            <el-button v-if="auth.isAdmin" size="small" type="danger" @click="deletePage(currentPage.id)">删除</el-button>
          </div>
        </div>
        <div class="wiki-content-meta">
          <el-tag :type="statusType(currentPage.status)">{{ currentPage.status }}</el-tag>
          <span>分类：{{ currentPage.category }}</span>
          <span>版本：v{{ currentPage.version }}</span>
          <span>来源：{{ currentPage.compiled_by === 'llm' ? 'LLM 编译' : '人工编辑' }}</span>
        </div>
        <div v-if="currentPage.summary" class="wiki-summary">{{ currentPage.summary }}</div>

        <!-- 查看模式 -->
        <div v-if="!editing" class="wiki-body" v-html="renderedContent"></div>

        <!-- 编辑模式 -->
        <div v-else class="wiki-editor">
          <el-input v-model="editForm.title" placeholder="标题" style="margin-bottom: 8px;" />
          <el-input v-model="editForm.category" placeholder="分类" style="margin-bottom: 8px;" />
          <el-input v-model="editForm.summary" placeholder="摘要" style="margin-bottom: 8px;" />
          <el-input v-model="editForm.content_md" type="textarea" :rows="15" placeholder="Markdown 内容" />
          <div style="margin-top: 8px;">
            <el-button type="primary" @click="saveEdit">保存</el-button>
            <el-button @click="editing = false">取消</el-button>
          </div>
        </div>

        <!-- 双链 -->
        <div v-if="currentPage.links?.length || currentPage.backlinks?.length" class="wiki-links">
          <h4>🔗 关联</h4>
          <div v-for="link in currentPage.links" :key="'l'+link.id" class="wiki-link">
            → <router-link :to="`/wiki/${link.to_page_id}`">{{ link.to_title }}</router-link>
          </div>
          <div v-for="bl in currentPage.backlinks" :key="'b'+bl.id" class="wiki-link">
            ← <router-link :to="`/wiki/${bl.from_page_id}`">{{ bl.from_title }}</router-link>
          </div>
        </div>

        <!-- 版本历史 -->
        <div v-if="currentPage.versions?.length" class="wiki-versions">
          <h4>📋 版本历史</h4>
          <div v-for="v in currentPage.versions" :key="v.id" class="wiki-version">
            v{{ v.version }} ({{ v.changed_by }}) {{ v.created_at }}
          </div>
        </div>
      </div>
    </div>

    <!-- 新建对话框 -->
    <el-dialog v-model="showCreate" title="新建知识页" width="500">
      <el-form :model="createForm">
        <el-form-item label="标题"><el-input v-model="createForm.title" /></el-form-item>
        <el-form-item label="分类"><el-input v-model="createForm.category" /></el-form-item>
        <el-form-item label="摘要"><el-input v-model="createForm.summary" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="createForm.content_md" type="textarea" :rows="8" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="createPage">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { Plus } from '@element-plus/icons-vue'
import api from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { renderMarkdown } from './chat/markdown'

const auth = useAuthStore()
const route = useRoute()

const pages = ref([])
const currentPage = ref(null)
const editing = ref(false)
const showCreate = ref(false)

const editForm = ref({ title: '', category: '', summary: '', content_md: '' })
const createForm = ref({ title: '', category: '未分类', summary: '', content_md: '' })

const renderedContent = computed(() => {
  if (!currentPage.value?.content_md) return ''
  return renderMarkdown(currentPage.value.content_md)
})

const statusType = (s) => ({ published: 'success', draft: 'info', stale: 'warning', archived: 'danger' }[s] || 'info')

async function loadPages() {
  try {
    const { data } = await api.get('/wiki/pages')
    pages.value = data.data || []
  } catch (e) { ElMessage.error('加载知识页失败') }
}

async function loadPage(id) {
  try {
    const { data } = await api.get(`/wiki/pages/${id}`)
    currentPage.value = data.data
    editing.value = false
  } catch (e) { ElMessage.error('加载页面详情失败') }
}

function startEdit() {
  editForm.value = {
    title: currentPage.value.title,
    category: currentPage.value.category,
    summary: currentPage.value.summary,
    content_md: currentPage.value.content_md,
  }
  editing.value = true
}

async function saveEdit() {
  try {
    await api.put(`/wiki/pages/${currentPage.value.id}`, editForm.value)
    editing.value = false
    await loadPage(currentPage.value.id)
    await loadPages()
  } catch (e) { ElMessage.error('保存失败') }
}

async function createPage() {
  try {
    await api.post('/wiki/pages', createForm.value)
    showCreate.value = false
    createForm.value = { title: '', category: '未分类', summary: '', content_md: '' }
    await loadPages()
  } catch (e) { ElMessage.error('创建失败') }
}

async function deletePage(id) {
  try {
    await ElMessageBox.confirm('确认删除该知识页？', '提示', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/wiki/pages/${id}`)
    currentPage.value = null
    await loadPages()
  } catch (e) { ElMessage.error('删除失败') }
}

onMounted(() => {
  loadPages()
  if (route.params.id) loadPage(Number(route.params.id))
})
</script>

<style scoped>
.wiki-view { display: flex; height: calc(100vh - 60px); }
.wiki-sidebar { width: 280px; border-right: 1px solid var(--border-color, #e4e7ed); overflow-y: auto; }
.wiki-sidebar-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border-color, #e4e7ed); }
.wiki-sidebar-header h3 { margin: 0; font-size: 14px; }
.wiki-list { padding: 8px; }
.wiki-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.wiki-item:hover { background: var(--bg-hover, #f5f7fa); }
.wiki-item.active { background: var(--bg-active, #ecf5ff); }
.wiki-item-title { font-size: 13px; font-weight: 500; margin-bottom: 4px; }
.wiki-item-meta { display: flex; gap: 6px; align-items: center; font-size: 11px; color: var(--text-secondary, #909399); }
.wiki-empty { padding: 20px; text-align: center; color: var(--text-secondary, #909399); font-size: 13px; }
.wiki-content { flex: 1; overflow-y: auto; padding: 20px 24px; }
.wiki-placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-secondary, #909399); }
.wiki-content-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.wiki-content-header h2 { margin: 0; }
.wiki-content-meta { display: flex; gap: 12px; font-size: 12px; color: var(--text-secondary, #909399); margin-bottom: 12px; }
.wiki-summary { padding: 8px 12px; background: var(--bg-tertiary, #f5f6f8); border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
.wiki-body { line-height: 1.7; }
.wiki-body :deep(table) { border-collapse: collapse; margin: 12px 0; }
.wiki-body :deep(th), .wiki-body :deep(td) { border: 1px solid var(--border-color, #e4e7ed); padding: 6px 10px; font-size: 13px; }
.wiki-links { margin-top: 20px; padding-top: 12px; border-top: 1px solid var(--border-color, #e4e7ed); }
.wiki-links h4 { margin: 0 0 8px; font-size: 13px; }
.wiki-link { font-size: 13px; margin-bottom: 4px; }
.wiki-link a { color: var(--accent, #409eff); text-decoration: none; }
.wiki-versions { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border-color, #e4e7ed); }
.wiki-versions h4 { margin: 0 0 8px; font-size: 13px; }
.wiki-version { font-size: 12px; color: var(--text-secondary, #909399); margin-bottom: 2px; }
.wiki-editor { margin-top: 12px; }
</style>
