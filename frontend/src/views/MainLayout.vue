<template>
  <div class="app-layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <span style="font-size:20px">☰</span>
        <span>伏羲 · 知识库</span>
      </div>
      <nav class="sidebar-nav">
        <div class="category-item" :class="{ active: route.path === '/' }" @click="$router.push('/')">
          <el-icon><ChatDotRound /></el-icon> 对话
        </div>
        <div class="category-item" :class="{ active: route.path.startsWith('/documents') }" @click="$router.push('/documents')">
          <el-icon><Folder /></el-icon> {{ auth.isAdmin ? '文档管理' : '文档浏览' }}
        </div>
        <div class="category-item" :class="{ active: route.path === '/graph' }" @click="$router.push('/graph')">
          <el-icon><Connection /></el-icon> 知识图谱
        </div>
        <!-- 仅管理员可见：插件管理 -->
        <div v-if="auth.isAdmin" class="category-item" :class="{ active: route.path === '/plugins' }" @click="$router.push('/plugins')">
          <el-icon><Grid /></el-icon> 插件
        </div>
        <div style="margin-top:24px;border-top:1px solid var(--border);padding-top:12px">
          <div class="user-badge" :class="{ 'user-badge--admin': auth.isAdmin }">
            {{ auth.username }} · {{ auth.isAdmin ? '管理员' : '用户' }}
          </div>
          <div class="category-item" @click="handleLogout">
            <el-icon><SwitchButton /></el-icon> 退出
          </div>
        </div>
      </nav>
    </aside>

    <!-- 主内容 -->
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.user-badge {
  font-size: 12px;
  color: var(--text-tertiary, #999);
  padding: 6px 0 10px;
  margin-bottom: 4px;
}
.user-badge--admin {
  color: var(--el-color-primary, #409eff);
  font-weight: 600;
}
</style>
