<template>
  <div class="chat-container">
    <div class="chat-messages" ref="msgContainer">
      <div v-if="messages.length === 0" class="empty-state">
        <el-icon style="font-size:48px"><ChatDotRound /></el-icon>
        <p style="margin-top:16px">输入问题，开始搜索文档</p>
        <p style="font-size:12px;margin-top:4px">支持 PDF、PPT、XLSX、DOCX 等多种格式</p>
      </div>
      <div v-for="(msg, i) in messages" :key="i" class="message" :class="msg.role">
        <div class="message-avatar">{{ msg.role === 'user' ? 'U' : 'AI' }}</div>
        <div class="message-body">
          <div class="message-content" v-html="renderAnswer(msg)"></div>

          <!-- 引用卡片：知识点来源 + 精确位置锚点 -->
          <div v-if="msg.sources && msg.sources.length" class="message-sources">
            <div class="sources-title">引用来源</div>
            <div
              v-for="s in msg.sources"
              :key="'src-' + (s.ref ?? s.chunk_id ?? s.file_name)"
              class="source-card"
              :ref="el => setSourceRef(s, el)"
              @click="goToSource(s)"
            >
              <sup class="source-ref">[{{ s.ref }}]</sup>
              <div class="source-meta">
                <span class="source-file">{{ s.file_name }}</span>
                <span v-if="s.chunk_index !== null && s.chunk_index !== undefined" class="source-loc">
                  第 {{ s.chunk_index }} 段
                </span>
              </div>
              <div v-if="s.content" class="source-snippet">{{ s.content }}</div>
            </div>
          </div>
        </div>
      </div>
      <div v-if="loading" class="message assistant">
        <div class="message-avatar">AI</div>
        <div class="message-content" style="color:var(--text-tertiary)">思考中...</div>
      </div>
    </div>

    <div class="chat-input-area">
      <el-input
        v-model="query"
        placeholder="输入问题，搜索知识库..."
        size="large"
        @keyup.enter="send"
        :disabled="loading"
      >
        <template #append>
          <el-button :icon="Promotion" @click="send" :loading="loading" type="primary">发送</el-button>
        </template>
      </el-input>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Promotion } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import api from '../api'

const query = ref('')
const loading = ref(false)
const messages = ref([])
const msgContainer = ref(null)
const router = useRouter()

marked.setOptions({ breaks: true })

// 每个引用编号 → 对应卡片 DOM（用于脚注点击滚动定位）
const sourceEls = {}
const currentSources = ref([])

function setSourceRef(s, el) {
  if (el) sourceEls[s.ref] = el
}

function renderMarkdown(text) {
  if (!text) return ''
  // XSS 防护：marked 输出后经 DOMPurify 消毒，避免 LLM/文档内容注入脚本
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}

// 把答案里的 [编号] 转成可点击的脚注（点击滚动到对应引用卡片）
function renderAnswer(msg) {
  let html = renderMarkdown(msg.content)
  if (!msg.sources || !msg.sources.length) return html

  // 只在正文里替换 [数字]（不替换已在标题/列表里的，简单全局替换即可，编号唯一）
  html = html.replace(/\[(\d{1,3})\]/g, (m, num) => {
    // 只有该编号存在于 sources 里才渲染成脚注，否则保留原文
    const exists = msg.sources.some(s => String(s.ref) === String(num))
    if (!exists) return m
    return `<sup class="cite-mark" data-ref="${num}" onclick="window.__jumpToSource('${num}')">[${num}]</sup>`
  })
  return html
}

// 脚注点击：滚动到对应引用卡片
function jumpToSource(num) {
  const el = sourceEls[num]
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('source-card--highlight')
    setTimeout(() => el.classList.remove('source-card--highlight'), 1500)
  }
}

// 引用卡片点击：跳转到文档详情（带段落锚点）
function goToSource(s) {
  if (!s.file_id) return
  const loc = s.chunk_index !== null && s.chunk_index !== undefined ? s.chunk_index : ''
  router.push({
    path: `/document/${s.file_id}`,
    query: loc !== '' ? { chunk: loc } : {}
  })
}

// 暴露给全局 onclick（v-html 内联 onclick 无法直接绑 Vue 方法，用全局桥接）
if (typeof window !== 'undefined') {
  window.__jumpToSource = (num) => {
    // 找到当前组件实例太麻烦，改用事件派发
    window.dispatchEvent(new CustomEvent('jump-source', { detail: num }))
  }
}

import { onMounted, onUnmounted } from 'vue'
onMounted(() => window.addEventListener('jump-source', onJumpSource))
onUnmounted(() => window.removeEventListener('jump-source', onJumpSource))
function onJumpSource(e) {
  jumpToSource(e.detail)
}

async function send() {
  const q = query.value.trim()
  if (!q || loading.value) return
  messages.value.push({ role: 'user', content: q })
  query.value = ''
  loading.value = true
  await scrollToBottom()
  try {
    const { data } = await api.post('/chat', { query: q, top_k: 10 })
    const d = data.data
    messages.value.push({
      role: 'assistant',
      content: d.answer,
      sources: d.sources || []
    })
  } catch (e) {
    messages.value.push({ role: 'assistant', content: '抱歉，请求失败: ' + (e.response?.data?.detail || e.message) })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

async function scrollToBottom() {
  await nextTick()
  if (msgContainer.value) {
    msgContainer.value.scrollTop = msgContainer.value.scrollHeight
  }
}
</script>

<style scoped>
.message-body {
  flex: 1;
  min-width: 0;
}

:deep(.cite-mark) {
  color: var(--el-color-primary, #409eff);
  cursor: pointer;
  font-weight: 600;
  padding: 0 2px;
  border-radius: 3px;
  transition: background 0.2s;
}
:deep(.cite-mark:hover) {
  background: rgba(64, 158, 255, 0.15);
}

.message-sources {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border, #e5e5e5);
}
.sources-title {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  margin-bottom: 8px;
}
.source-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--bg-elevated, #f7f8fa);
  border: 1px solid var(--border, #e5e5e5);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.source-card:hover {
  border-color: var(--el-color-primary, #409eff);
  box-shadow: 0 1px 6px rgba(0,0,0,0.08);
}
.source-card--highlight {
  border-color: var(--el-color-primary, #409eff);
  box-shadow: 0 0 0 2px rgba(64,158,255,0.3);
}
.source-ref {
  color: var(--el-color-primary, #409eff);
  font-weight: 700;
  margin-right: 4px;
}
.source-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.source-file {
  font-weight: 600;
  color: var(--text-primary, #333);
}
.source-loc {
  font-size: 12px;
  color: var(--el-color-primary, #409eff);
  background: rgba(64,158,255,0.1);
  padding: 1px 8px;
  border-radius: 10px;
}
.source-snippet {
  font-size: 12px;
  color: var(--text-secondary, #666);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
