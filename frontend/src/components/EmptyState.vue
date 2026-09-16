<template>
  <div class="empty-state" role="status" :aria-label="title">
    <el-icon><component :is="icon" /></el-icon>
    <p>{{ title }}</p>
    <p v-if="hint" class="empty-state-hint">{{ hint }}</p>
    <div v-if="$slots.action" class="empty-action"><slot name="action" /></div>
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: 'Folder' },
  title: { type: String, default: '暂无数据' },
  hint: { type: String, default: '' },
})
</script>

<style scoped>
.empty-state-hint {
  font-size: 12px;
  margin-top: 4px;
  color: var(--text-disabled);
}
.empty-action {
  margin-top: 16px;
}
</style>
