<template>
  <div class="app-layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-logo">伏</div>
        <div>
          <div class="sidebar-title">伏羲 · 知识库</div>
          <div class="sidebar-sub">工业 RAG 平台</div>
        </div>
      </div>
      <nav class="sidebar-nav" role="navigation" aria-label="主导航">
        <div class="nav-group-label">工作台</div>
        <div class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/' }" @click="navigateTo('/')" @keydown.enter="navigateTo('/')" @keydown.space.prevent="navigateTo('/')">
          <el-icon><ChatDotRound /></el-icon> 对话
        </div>
        <div class="category-item" tabindex="0" role="link" :class="{ active: route.path.startsWith('/documents') }" @click="navigateTo('/documents')" @keydown.enter="navigateTo('/documents')" @keydown.space.prevent="navigateTo('/documents')">
          <el-icon><Folder /></el-icon> {{ auth.isAdmin ? '文档管理' : '文档浏览' }}
        </div>
        <div class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/graph' }" @click="navigateTo('/graph')" @keydown.enter="navigateTo('/graph')" @keydown.space.prevent="navigateTo('/graph')">
          <el-icon><Connection /></el-icon> 知识图谱
        </div>
        <div class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/debug' }" @click="navigateTo('/debug')" @keydown.enter="navigateTo('/debug')" @keydown.space.prevent="navigateTo('/debug')">
          <el-icon><Aim /></el-icon> 检索调试
        </div>
        <!-- 仅管理员可见：插件管理 -->
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/plugins' }" @click="navigateTo('/plugins')" @keydown.enter="navigateTo('/plugins')" @keydown.space.prevent="navigateTo('/plugins')">
          <el-icon><Grid /></el-icon> 插件
        </div>
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/mcp' }" @click="navigateTo('/mcp')" @keydown.enter="navigateTo('/mcp')" @keydown.space.prevent="navigateTo('/mcp')">
          <el-icon><Connection /></el-icon> MCP 市场
        </div>
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/dms' }" @click="navigateTo('/dms')" @keydown.enter="navigateTo('/dms')" @keydown.space.prevent="navigateTo('/dms')">
          <el-icon><FolderOpened /></el-icon> DMS 文档源
        </div>
        <div class="category-item" tabindex="0" role="link" :class="{ active: route.path.startsWith('/wiki') }" @click="navigateTo('/wiki')" @keydown.enter="navigateTo('/wiki')" @keydown.space.prevent="navigateTo('/wiki')">
          <el-icon><Notebook /></el-icon> Wiki 知识页
        </div>
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/config' }" @click="navigateTo('/config')" @keydown.enter="navigateTo('/config')" @keydown.space.prevent="navigateTo('/config')">
          <el-icon><Setting /></el-icon> 系统配置
        </div>
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/recycle-bin' }" @click="navigateTo('/recycle-bin')" @keydown.enter="navigateTo('/recycle-bin')" @keydown.space.prevent="navigateTo('/recycle-bin')">
          <el-icon><Delete /></el-icon> 回收站
        </div>
        <div v-if="auth.isAdmin" class="category-item" tabindex="0" role="link" :class="{ active: route.path === '/audit' }" @click="navigateTo('/audit')" @keydown.enter="navigateTo('/audit')" @keydown.space.prevent="navigateTo('/audit')">
          <el-icon><List /></el-icon> 审计日志
        </div>
        <div style="margin-top:24px;border-top:1px solid var(--border);padding-top:12px">
          <div class="user-badge" :class="{ 'user-badge--admin': auth.isAdmin }">
            <span class="user-avatar">{{ (auth.username || '?')[0] }}</span>
            {{ auth.username }} · {{ auth.isAdmin ? '管理员' : '用户' }}
          </div>
          <div class="category-item" tabindex="0" role="link" @click="handleLogout" @keydown.enter="handleLogout" @keydown.space.prevent="handleLogout">
            <el-icon><SwitchButton /></el-icon> 退出
          </div>
        </div>
      </nav>
    </aside>

    <!-- 主内容 -->
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navigateTo = (path) => {
  router.push(path)
}

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.sidebar-logo {
  width: 34px;
  height: 34px;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 700;
  font-family: var(--font-display);
  box-shadow: var(--shadow);
  position: relative;
}
/* 工业精工：右下角暖铜橙小圆点（仪表盘高亮点） */
.sidebar-logo::after {
  content: '';
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--accent-warm);
  border: 2px solid var(--bg-primary);
}
.sidebar-title {
  font-weight: 700;
  font-size: 15px;
  line-height: 1.3;
  font-family: var(--font-display);
  letter-spacing: 0.5px;
}
.sidebar-sub {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 400;
  font-family: var(--font-mono);
  letter-spacing: 0.5px;
}
.nav-group-label {
  font-size: 11px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 1.2px;
  padding: 14px 14px 8px;
  font-weight: 700;
  font-family: var(--font-mono);
  /* 工业精工：细分隔线带暖铜橙小刻度 */
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
}
.nav-group-label::after {
  content: '';
  display: inline-block;
  width: 14px;
  height: 2px;
  background: var(--accent-warm);
  margin-left: 6px;
  vertical-align: middle;
}
.user-badge {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 6px 8px 10px;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}
.user-badge--admin {
  color: var(--accent);
  font-weight: 600;
}
.user-avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--accent-light);
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
}
</style>
