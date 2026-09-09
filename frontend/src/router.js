import { createRouter, createWebHashHistory } from 'vue-router'
import MainLayout from './views/MainLayout.vue'

const routes = [
  {
    path: '/',
    component: MainLayout,
    children: [
      { path: '', name: 'Chat', component: () => import('./views/ChatView.vue') },
      { path: 'documents', name: 'Documents', component: () => import('./views/DocumentsView.vue') },
      { path: 'graph', name: 'Graph', component: () => import('./views/GraphView.vue') },
      { path: 'debug', name: 'Debug', component: () => import('./views/DebugView.vue') },
      { path: 'plugins', name: 'Plugins', component: () => import('./views/PluginsView.vue') },
      { path: 'mcp', name: 'McpMarket', component: () => import('./views/McpMarket.vue') },
      { path: 'config', name: 'Config', component: () => import('./views/ConfigView.vue') },
      { path: 'dms', name: 'DmsImport', component: () => import('./views/DmsImport.vue') },
      { path: 'document/:id', name: 'DocumentDetail', component: () => import('./views/DocumentDetail.vue') },
    ]
  },
  { path: '/login', name: 'Login', component: () => import('./views/LoginView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 路由守卫：检查 token 有效性（含过期检测）
router.beforeEach((to, from) => {
  const token = localStorage.getItem('token')
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      if (payload.exp && payload.exp * 1000 < Date.now()) {
        localStorage.removeItem('token')
        localStorage.removeItem('role')
        return '/login'
      }
    } catch {
      // token 格式异常，清除
      localStorage.removeItem('token')
      localStorage.removeItem('role')
      return '/login'
    }
  }
  const role = localStorage.getItem('role') || 'user'
  if ((to.path === '/plugins' || to.path === '/mcp' || to.path === '/config' || to.path === '/dms') && role !== 'admin') {
    return '/'
  }
  return true
})

export default router
