<template>
  <router-view />
</template>

<script setup>
import { watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

watch(() => route.path, (path) => {
  if (path !== '/login' && !auth.isLoggedIn) {
    router.push('/login')
  }
}, { immediate: true })
</script>
