<template>
  <div class="skeleton-block" :style="{ width, height, borderRadius: radius }">
    <div class="skeleton-shimmer" />
  </div>
</template>
<script setup>
defineProps({ width: { default: '100%' }, height: { default: '16px' }, radius: { default: '6px' } })
</script>
<style scoped>
.skeleton-block { background: var(--bg-subtle, #f0f0f0); position: relative; overflow: hidden; }
.skeleton-shimmer { position: absolute; inset: 0; background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.4) 50%, transparent 100%); animation: shimmer 1.5s infinite; }
@keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
</style>
