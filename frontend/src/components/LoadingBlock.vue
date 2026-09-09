<template>
  <div class="loading-center">
    <el-icon class="is-loading" :style="{ fontSize: size }">
      <Loading />
    </el-icon>
    <span v-if="text" class="loading-text">{{ text }}</span>
  </div>
</template>

<script setup>
import { Loading } from '@element-plus/icons-vue'

defineProps({
  size: { type: String, default: '32px' },
  text: { type: String, default: '' },
})
</script>

<style scoped>
.loading-text {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
