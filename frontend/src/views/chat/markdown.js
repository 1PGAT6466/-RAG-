// chat/markdown.js — 对话回复的 markdown 渲染 + 引用脚注转换（纯函数）
//
// 从 ChatView.vue 抽取，把「标记渲染 + XSS 消毒 + [编号] 脚注转换」这组纯逻辑
// 收归一处，便于复用与单测，减轻视图组件的上帝倾向。
// 不依赖任何响应式状态，输入 msg（含 content/sources），输出 HTML 字符串。

import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true })

// XSS 防护：marked 输出后经 DOMPurify 消毒，避免 LLM/文档内容注入脚本
export function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}

// 把答案里的 [编号] 转成可点击的脚注（点击滚动到对应引用卡片）
// 只在正文里替换真实存在于 sources 里的编号，否则保留原文（避免杜撰编号被渲染成假脚注）
export function renderAnswer(msg) {
  let html = renderMarkdown(msg.content)
  if (!msg.sources || !msg.sources.length) return html

  html = html.replace(/\[(\d{1,3})\]/g, (m, num) => {
    const exists = msg.sources.some(s => String(s.ref) === String(num))
    if (!exists) return m
    return `<sup class="cite-mark" data-ref="${num}" onclick="window.__jumpToSource('${num}')">[${num}]</sup>`
  })
  return html
}
