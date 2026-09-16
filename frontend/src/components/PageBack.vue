<template>
  <div class="page-back" @click="goBack" role="button" :aria-label="label || '返回'" tabindex="0" @keydown.enter="goBack" @keydown.space.prevent="goBack">
    <el-icon><ArrowLeft /></el-icon>
    <span>{{ label || '返回' }}</span>
  </div>
</template>

<script setup>
import { ArrowLeft } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  to: { type: String, default: '' },
  label: { type: String, default: '' },
})

const router = useRouter()

function goBack() {
  if (props.to) {
    router.push(props.to)
  } else {
    router.back()
  }
}
</script>

<style scoped>
.page-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text-secondary, #666);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  margin-bottom: 12px;
  transition: color 0.15s, background 0.15s;
  user-select: none;
}
.page-back:hover {
  color: var(--accent, #0e6e6a);
  background: var(--bg-hover, #f0f1f3);
}
</style>
