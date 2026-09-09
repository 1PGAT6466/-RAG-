<template>
  <div class="empty-state">
    <el-icon><component :is="icon" /></el-icon>
    <p>{{ title }}</p>
    <p v-if="hint" class="empty-state-hint">{{ hint }}</p>
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
</style>
