<template>
  <div class="chat-page">
    <!-- 左侧历史会话侧栏 -->
    <aside class="chat-sidebar">
      <div class="chat-sidebar-head">
        <el-button type="primary" size="small" :icon="Plus" @click="newConversation" style="width:100%">
          新建对话
        </el-button>
      </div>
      <div class="chat-sidebar-label">历史会话</div>
      <div class="conversation-list">
        <div v-if="conversations.length === 0" class="conv-empty">暂无历史会话</div>
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conversation-item"
          :class="{ active: activeConversationId === c.id }"
          @click="openConversation(c.id)"
        >
          <div class="conversation-title">{{ c.title }}</div>
          <div class="conversation-meta">
            <span>{{ c.msg_count }} 条</span>
            <el-icon class="conv-del" @click.stop="deleteConversation(c.id)"><Delete /></el-icon>
          </div>
        </div>
      </div>
    </aside>

    <!-- 右侧对话区 -->
    <div class="chat-container">
      <div class="chat-messages" ref="msgContainer">
        <div class="chat-messages-inner">
        <div v-if="messages.length === 0" class="chat-empty">
          <div class="chat-empty-mark">
            <span class="chat-empty-glyph">伏</span>
            <i class="chat-empty-dot"></i>
          </div>
          <div class="chat-empty-title">开始与伏羲对话</div>
          <p class="chat-empty-sub">支持自动检索知识库、联网搜索、自由闲聊三种模式</p>
          <div class="chat-empty-chips">
            <span class="chat-empty-chip" @click="quickAsk('什么是阻抗匹配？')">🔍 什么是阻抗匹配？</span>
            <span class="chat-empty-chip" @click="quickAsk('FAKRA 连接器的选型要点')">📄 FAKRA 连接器的选型要点</span>
            <span class="chat-empty-chip" @click="quickAsk('镀金层厚度标准')">📐 镀金层厚度标准</span>
          </div>
        </div>
        <div v-for="(msg, i) in messages" :key="i" class="message" :class="msg.role">
          <div class="message-body">
            <div class="message-meta-row">
              <span class="message-role-label" :class="msg.role">{{ msg.role === 'user' ? '我' : '伏羲' }}</span>
              <span class="message-time" v-if="msg.ts">{{ formatTime(msg.ts) }}</span>
            </div>
            <div class="message-content" v-html="htmlFor(i)"></div>

            <!-- 参考来源：规整的章节引用列表（每个回复都有） -->
            <div v-if="msg.role === 'assistant' && msg.sources && msg.sources.length" class="message-sources">
              <div class="sources-title">参考来源</div>
              <div
                v-for="s in msg.sources"
                :key="'src-' + (s.ref ?? s.chunk_id ?? s.file_name)"
                class="source-row"
                :ref="el => setSourceRef(s, el)"
                @click="goToSource(s)"
              >
                <div class="source-row-head">
                  <span class="source-ref">[{{ s.ref }}]</span>
                  <span class="source-file" :title="s.file_name">{{ s.file_name }}</span>
                  <span v-if="s.chunk_index !== null && s.chunk_index !== undefined" class="source-loc">
                    {{ s.chunk_index }} 段
                  </span>
                </div>
                <div v-if="s.content" class="source-snippet">{{ s.content }}</div>
              </div>
            </div>

            <!-- 对话操作条：重新生成 / 复制 / 赞 / 踩（仅 assistant 消息） -->
            <div v-if="msg.role === 'assistant' && msg.content && !loading" class="message-actions">
              <button class="msg-action" title="重新生成" @click="regenerate(i)">
                <el-icon><Refresh /></el-icon><span>重新生成</span>
              </button>
              <button class="msg-action" title="复制" @click="copyAnswer(msg)">
                <el-icon><Document /></el-icon><span>复制</span>
              </button>
              <span class="msg-action-sep"></span>
              <button
                class="msg-action msg-action--vote"
                :class="{ active: msg._feedback === 'up' }"
                title="回答有帮助"
                @click="vote(msg, 'up')"
              >
                <el-icon><Top /></el-icon>
              </button>
              <button
                class="msg-action msg-action--vote"
                :class="{ active: msg._feedback === 'down' }"
                title="回答不准确"
                @click="vote(msg, 'down')"
              >
                <el-icon><Bottom /></el-icon>
              </button>
            </div>
          </div>
        </div>
        <div v-if="loading" class="message assistant">
          <div class="message-body">
            <div class="message-meta-row">
              <span class="message-role-label assistant">伏羲</span>
            </div>
            <div class="message-content message-content--loading">思考中...</div>
          </div>
        </div>
        </div>
      </div>

      <div class="chat-input-area">
        <div class="chat-input-inner">
          <div class="chat-mode-bar">
            <el-radio-group v-model="mode" size="small">
              <el-radio-button value="auto">自动</el-radio-button>
              <el-radio-button value="knowledge">知识库</el-radio-button>
              <el-radio-button value="chat">闲聊</el-radio-button>
              <el-radio-button value="web">联网</el-radio-button>
            </el-radio-group>
          </div>
          <div class="chat-input-box">
            <textarea
              v-model="query"
              class="chat-input-textarea"
              :placeholder="inputPlaceholder"
              rows="1"
              @keydown.enter.exact.prevent="send"
              @input="autoGrow"
              :disabled="loading"
            ></textarea>
            <button
              class="chat-send-btn"
              :disabled="loading || !query.trim()"
              @click="send"
              :title="'发送'">
              <el-icon><Promotion /></el-icon>
            </button>
          </div>
          <div class="chat-input-hint">
            <span>Enter 发送 · Shift+Enter 换行 · 支持多轮对话</span>
            <span class="chat-input-mode-hint">{{ modeHint }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Promotion, Plus, Delete, Top, Bottom } from '@element-plus/icons-vue'
// ElMessage/ElMessageBox 由 unplugin-auto-import 自动引入（含样式），无需显式 import
import { renderAnswer } from './chat/markdown'
import { formatTime, extractChunkIds, autoGrow } from './chat/helpers'
import api from '../api'
import feedbackApi from '../api/feedback'

const query = ref('')
const loading = ref(false)
const messages = ref([])
const msgContainer = ref(null)
const router = useRouter()
const route = useRoute()
const mode = ref('auto')
const modeHint = computed(() => {
  const m = { auto: '自动：智能判断检索/联网/闲聊', knowledge: '知识库：仅检索本地文档', chat: '闲聊：自由对话', web: '联网：实时信息' }
  return m[mode.value] || ''
})
const inputPlaceholder = computed(() => {
  const p = { auto: '输入问题，智能检索知识库…', knowledge: '输入问题，检索本地文档…', chat: '自由聊天…', web: '输入问题，联网搜索实时信息…' }
  return p[mode.value] || p.auto
})

// 会话状态
const conversations = ref([])
const activeConversationId = ref(null)

// 每个引用编号 → 对应卡片 DOM（用于脚注点击滚动定位）
const sourceEls = {}
const currentSources = ref([])

function setSourceRef(s, el) {
  if (el) sourceEls[s.ref] = el
}


// 预渲染 HTML 缓存（每条消息只解析一次，避免消息列表增长后每次渲染重算全部历史）
const renderedHtml = computed(() => {
  const map = new Map()
  messages.value.forEach((msg, i) => {
    map.set(i, renderAnswer(msg))
  })
  return map
})

// 按下标取缓存（供模板 v-html）
function htmlFor(i) {
  return renderedHtml.value.get(i) || ''
}

// 脚注点击：滚动到对应引用卡片
function jumpToSource(num) {
  const el = sourceEls[num]
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('source-row--highlight')
    setTimeout(() => el.classList.remove('source-row--highlight'), 1500)
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

onMounted(() => {
  window.addEventListener('jump-source', onJumpSource)
  fetchConversations()
  // 第 2 项：从文档详情「被引用」角标跳回，带 conversation 参数打开对应会话
  const convParam = route.query.conversation
  if (convParam) {
    const cid = Number(convParam)
    if (cid) openConversation(cid)
  }
})
onUnmounted(() => window.removeEventListener('jump-source', onJumpSource))
function onJumpSource(e) {
  jumpToSource(e.detail)
}

// === 会话管理 ===
async function fetchConversations() {
  try {
    const { data } = await api.get('/conversations')
    conversations.value = data.data || []
  } catch (e) {
    // 加载失败不阻断
  }
}

function newConversation() {
  activeConversationId.value = null
  messages.value = []
}

// 空状态快捷提问：填入输入框并直接发送
function quickAsk(q) {
  query.value = q
  send()
}

async function openConversation(id) {
  activeConversationId.value = id
  try {
    const { data } = await api.get(`/conversations/${id}`)
    const msgs = data.data.messages || []
    messages.value = msgs.map(m => ({
      role: m.role,
      content: m.content,
      sources: m.sources || [],
    }))
    await scrollToBottom()
  } catch (e) {
    ElMessage.error('加载会话失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function deleteConversation(id) {
  try {
    await ElMessageBox.confirm('删除该会话？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.delete(`/conversations/${id}`)
    ElMessage.success('已删除')
    if (activeConversationId.value === id) {
      activeConversationId.value = null
      messages.value = []
    }
    await fetchConversations()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function send() {
  const q = query.value.trim()
  if (!q || loading.value) return
  messages.value.push({ role: 'user', content: q, ts: Date.now() })
  query.value = ''
  loading.value = true
  await scrollToBottom()

  // 若当前无会话，先新建一个（后台）
  let cid = activeConversationId.value
  if (!cid) {
    try {
      const { data } = await api.post('/conversations', { title: q.slice(0, 30) })
      cid = data.data.id
      activeConversationId.value = cid
    } catch (e) {
      // 新建失败则退化为无会话的即时对话
      cid = null
    }
  }

  // 组装多轮历史（传给闲聊模式做上下文）。
  // 只保留最近 10 条历史，避免会话过长触发后端 history 长度上限（20 条）而 422 报错。
  const history = messages.value.slice(0, -1).slice(-10).map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.content }))
  try {
    // knowledge 模式走 SSE 流式，其余走普通 POST
    if (mode.value === 'knowledge' || mode.value === 'auto') {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify({ query: q, top_k: 10, mode: mode.value, history, conversation_id: cid }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let answer = ''
      let sources = []
      let sseDataLines = []
      messages.value.push({ role: 'assistant', content: '', sources: [], ts: Date.now(), _query: q })
      const idx = messages.value.length - 1
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        // 标准 SSE 事件级解析：一个事件由若干 data: 行 + 空行分隔。
        // 累积连续 data 行（用 \n join 还原换行），空行时提交事件。
        for (const line of lines) {
          if (line.startsWith('data:')) {
            // 去掉可选空格；data: 后若无空格也兼容
            sseDataLines.push(line.startsWith('data: ') ? line.slice(6) : line.slice(5))
          } else if (line.trim() === '' && sseDataLines.length) {
            // 事件边界：提交累积的 data 行
            const payload = sseDataLines.join('\n')
            sseDataLines = []
            if (payload === '[DONE]') continue
            if (payload.startsWith('__SOURCES__')) {
              try { sources = JSON.parse(payload.replace('__SOURCES__', '')) } catch {}
            } else {
              answer += payload
              messages.value[idx].content = answer
              await scrollToBottom()
            }
          }
        }
      }
      // 最后可能残留未提交的 data 行（流结束时无空行）
      if (sseDataLines.length) {
        const payload = sseDataLines.join('\n')
        sseDataLines = []
        if (payload !== '[DONE]' && !payload.startsWith('__SOURCES__')) {
          answer += payload
          messages.value[idx].content = answer
        }
      }
      messages.value[idx].sources = sources
    } else {
      const { data } = await api.post('/chat', {
        query: q, top_k: 10, mode: mode.value, history, conversation_id: cid,
      })
      const d = data.data
      messages.value.push({ role: 'assistant', content: d.answer, sources: d.sources || [], ts: Date.now(), _query: q })
    }
    // 刷新会话列表（标题/消息数/排序已更新）
    fetchConversations()
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

// 输入框自适应高度
// === 对话操作条：重新生成 / 复制 / 赞踩反馈 ===

// 复制纯文本答案（非 v-html 富文本）
async function copyAnswer(msg) {
  const text = msg.content || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      ElMessage.success('已复制到剪贴板')
    } catch (e2) {
      ElMessage.error('复制失败')
    }
  }
}

// 重新生成：覆盖当前这条 assistant 消息（保留其对应的 user 提问）
async function regenerate(i) {
  if (loading.value) return
  const msg = messages.value[i]
  if (!msg || msg.role !== 'assistant') return
  let q = ''
  for (let j = i - 1; j >= 0; j--) {
    if (messages.value[j].role === 'user') { q = messages.value[j].content; break }
  }
  if (!q) { ElMessage.warning('未找到可重新生成的问题'); return }
  messages.value.splice(i, messages.value.length - i)
  query.value = q
  await send()
}

// 赞/踩反馈：可切换、可撤销（再次点击同按钮取消）
async function vote(msg, kind) {
  const q = msg._query || ''
  const cids = extractChunkIds(msg)
  const prev = msg._feedback
  try {
    if (prev === kind) {
      await feedbackApi.remove(kind, q, cids)
      msg._feedback = null
    } else {
      if (prev) await feedbackApi.remove(prev, q, cids)
      await feedbackApi.send(kind, q, cids, activeConversationId.value ?? null)
      msg._feedback = kind
      ElMessage.success(kind === 'up' ? '已记录：有帮助' : '已记录：不准确')
    }
  } catch (e) {
    ElMessage.error('反馈失败: ' + (e.response?.data?.detail || e.message))
  }
}
</script>

<style scoped>
.chat-page {
  display: flex;
  height: 100%;
  overflow: hidden;
}

/* 右侧对话区（覆盖全局 .chat-container 的居中限宽，改为撑满剩余空间） */
/* 注意：本文件的 .message 系列样式在 scoped 内，与 global.css 同名类隔离，
   二字段内独立定制，对齐工业精工 tokens（不再用旧蓝紫 #409eff） */
.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  max-width: none;
  width: 100%;
  margin: 0;
}

/* 消息区居中限宽，输入区钉在底部 */
.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 28px 24px;
}
.chat-messages-inner {
  max-width: 760px;
  margin: 0 auto;
}

.chat-input-area {
  flex-shrink: 0;
  padding: 14px 20px 10px;
  background: rgba(255,255,255,0.55);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-top: 1px solid var(--border);
}
.chat-input-inner {
  max-width: 760px;
  margin: 0 auto;
}

.chat-mode-bar {
  margin-bottom: 10px;
  display: flex;
  justify-content: flex-start;
}

/* 精致的玻璃输入框 */
.chat-input-box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 8px 8px 8px 16px;
  border-radius: 16px;
  background: linear-gradient(150deg, rgba(255,255,255,0.85), rgba(255,255,255,0.6));
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255,255,255,0.7);
  box-shadow: 0 4px 20px rgba(31,45,41,0.08), inset 0 1px 0 rgba(255,255,255,0.9);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.chat-input-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 4px 24px rgba(14,110,106,0.14), 0 0 0 3px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.9);
}
.chat-input-textarea {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
  padding: 6px 0;
  font-family: inherit;
  max-height: 180px;
}
.chat-input-textarea::placeholder {
  color: var(--text-tertiary);
}
.chat-input-textarea:disabled {
  opacity: 0.6;
}
.chat-send-btn {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #13a89e 0%, #0e6e6a 100%);
  color: #fff;
  font-size: 18px;
  box-shadow: 0 4px 12px rgba(14,110,106,0.28);
  transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
}
.chat-send-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(14,110,106,0.36);
}
.chat-send-btn:active:not(:disabled) {
  transform: translateY(0);
}
.chat-send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}

.chat-input-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 6px 2px 0;
  letter-spacing: 0.3px;
}
.chat-input-mode-hint {
  font-family: var(--font-mono);
}

/* 左侧历史会话侧栏 */
.chat-sidebar {
  width: 240px;
  min-width: 240px;
  background: var(--bg-secondary, #fafafa);
  border-right: 1px solid var(--border, #e5e5e5);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chat-sidebar-head {
  padding: 12px;
  border-bottom: 1px solid var(--border, #e5e5e5);
}
.chat-sidebar-label {
  font-size: 11px;
  color: var(--text-tertiary, #999);
  padding: 10px 14px 6px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 12px;
}
.conv-empty {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  text-align: center;
  padding: 20px 0;
}
.conversation-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}
.conversation-item:hover {
  background: var(--bg-hover, #f0f0f0);
}
.conversation-item.active {
  background: var(--accent-light);
}
.conversation-item.active .conversation-title {
  color: var(--accent);
  font-weight: 600;
}
.conversation-title {
  font-size: 13px;
  color: var(--text-primary, #333);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.conversation-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary, #999);
}
.conv-del {
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}
.conversation-item:hover .conv-del {
  opacity: 1;
}
.conv-del:hover {
  color: var(--color-danger);
}

/* 右侧对话区：message-body 承载角色标签+气泡+时间+引用，按角色对齐 */
.message-body {
  max-width: 82%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.message.user .message-body {
  align-items: flex-end;
}

/* 消息空状态：品牌 logo + 分层文案 + 快捷提问 chips */
.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
}
.chat-empty-mark {
  position: relative;
  width: 64px;
  height: 64px;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--accent), var(--accent-hover));
  box-shadow: var(--shadow-md);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
}
.chat-empty-glyph {
  font-size: 28px;
  font-weight: 700;
  color: #fff;
}
.chat-empty-dot {
  position: absolute;
  right: -4px;
  bottom: -4px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--accent-warm);
  border: 3px solid var(--bg-elevated);
}
.chat-empty-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 6px;
}
.chat-empty-sub {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 24px;
}
.chat-empty-chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}
.chat-empty-chip {
  font-size: 12.5px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 7px 14px;
  cursor: pointer;
  transition: border-color var(--duration-fast), color var(--duration-fast), box-shadow var(--duration-fast);
}
.chat-empty-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
  box-shadow: var(--shadow-xs);
}

/* 消息：无头像，EasyClaw 风格玻璃气泡，规整统一 */
.message { display: flex; margin-bottom: 26px; }
.message.user { justify-content: flex-end; }
.message-meta-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
}
.message.user .message-meta-row { justify-content: flex-end; }
.message-role-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: var(--text-tertiary);
}
.message-role-label.user { color: var(--accent); }
.message-time {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
}
.message-content {
  width: 100%;
  padding: 14px 18px;
  border-radius: 14px;
  line-height: 1.7;
  font-size: 14px;
  background: linear-gradient(150deg, rgba(255,255,255,0.78), rgba(255,255,255,0.52));
  backdrop-filter: blur(16px) saturate(150%);
  -webkit-backdrop-filter: blur(16px) saturate(150%);
  border: 1px solid rgba(255,255,255,0.7);
  box-shadow: 0 4px 18px rgba(31,45,41,0.07), inset 0 1px 0 rgba(255,255,255,0.95);
}
.message.user .message-content {
  background: linear-gradient(135deg, rgba(14,110,106,0.92), rgba(11,79,76,0.92));
  color: #fff;
  border: 1px solid rgba(255,255,255,0.16);
  box-shadow: 0 6px 18px rgba(14,110,106,0.24), inset 0 1px 0 rgba(255,255,255,0.15);
}
.message.assistant .message-content {
  box-shadow: 0 4px 18px rgba(31,45,41,0.07), inset 0 1px 0 rgba(255,255,255,0.95);
}
.message-content--loading {
  color: var(--text-tertiary);
  white-space: nowrap;
}

:deep(.cite-mark) {
  color: var(--accent);
  cursor: pointer;
  font-weight: 700;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  padding: 0 3px;
  border-radius: 3px;
  transition: background var(--duration-fast), color var(--duration-fast);
}
:deep(.cite-mark:hover) {
  background: var(--accent-glow);
  color: var(--accent-warm);
}

.message-sources {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.sources-title {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
  letter-spacing: 0.5px;
  font-weight: 600;
}
/* 规整的章节引用行（头行：编号+文件名+段落位置；次行：摘要 snippet） */
.source-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 12px;
  margin-bottom: 6px;
  border-radius: 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: border-color var(--duration-fast), background var(--duration-fast), box-shadow var(--duration-fast);
}
.source-row:last-child { margin-bottom: 0; }
.source-row:hover {
  border-color: var(--accent);
  background: var(--accent-light);
}
.source-row--highlight {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.source-row-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.source-ref {
  color: var(--accent);
  font-weight: 700;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.source-file {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.source-loc {
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-glow);
  padding: 1px 8px;
  border-radius: 10px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.source-snippet {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  padding-left: 2px;
}

/* 对话操作条：重新生成 / 复制 / 赞 / 踩（工业精工，克制低调） */
.message-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 10px;
}
.msg-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 12px;
  font-family: inherit;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: color var(--duration-fast), background var(--duration-fast);
}
.msg-action:hover {
  color: var(--text-secondary);
  background: var(--bg-hover);
}
.msg-action .el-icon {
  font-size: 14px;
}
.msg-action-sep {
  width: 1px;
  height: 14px;
  background: var(--border);
  margin: 0 4px;
}
.msg-action--vote {
  font-size: 14px;
  padding: 4px 6px;
}
.msg-action--vote.active {
  color: var(--accent);
  background: var(--accent-light);
}
.msg-action--vote:hover {
  color: var(--accent);
}
</style>
