<template>
  <div class="schema-result">
    <!-- 错误 -->
    <div v-if="isError" class="result-error">
      <div class="result-error-title">调用失败</div>
      <pre>{{ data.data?.error || '未知错误' }}</pre>
    </div>
    <!-- 成功 -->
    <div v-else class="result-ok">
      <!-- 对象 → 键值展示 -->
      <div v-if="isObject" class="result-kv">
        <div v-for="(v, k) in value" :key="k" class="result-row">
          <span class="result-key">{{ k }}</span>
          <span class="result-val">{{ formatVal(v) }}</span>
        </div>
      </div>
      <!-- 数组 → 列表 -->
      <div v-else-if="isArray" class="result-list">
        <div v-for="(item, i) in value" :key="i" class="result-item">{{ formatVal(item) }}</div>
      </div>
      <!-- 标量 -->
      <div v-else class="result-scalar">{{ formatVal(value) }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  // 统一的 { status: 'ok'|'error', data: ... } 结构
  result: { type: Object, default: null },
})

const isError = computed(() => props.result?.status === 'error')

const value = computed(() => {
  if (isError.value) return null
  return props.result?.data ?? null
})

const isObject = computed(() => value.value !== null && typeof value.value === 'object' && !Array.isArray(value.value))
const isArray = computed(() => Array.isArray(value.value))

function formatVal(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
</script>

<style scoped>
.schema-result { font-size: 13px; }
.result-error { color: #f56c6c; }
.result-error-title { font-weight: 600; margin-bottom: 4px; }
.result-error pre { white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.result-ok { color: var(--text-primary); }
.result-row { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.result-key { font-weight: 600; min-width: 80px; color: var(--text-secondary); }
.result-val { word-break: break-all; }
.result-list { display: flex; flex-direction: column; gap: 6px; }
.result-item { padding: 6px 10px; background: var(--bg-secondary); border-radius: var(--radius); }
.result-scalar { padding: 8px 0; }
</style>
