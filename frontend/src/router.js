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
      { path: 'wiki', name: 'Wiki', component: () => import('./views/WikiView.vue') },
      { path: 'wiki/:id', name: 'WikiDetail', component: () => import('./views/WikiView.vue') },
      { path: 'document/:id', name: 'DocumentDetail', component: () => import('./views/DocumentDetail.vue') },
      { path: 'recycle-bin', name: 'RecycleBin', component: () => import('./views/RecycleBin.vue') },
      { path: 'audit', name: 'Audit', component: () => import('./views/AuditView.vue') },
    ]
  },
  { path: '/login', name: 'Login', component: () => import('./views/LoginView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 路由守卫：首次访问任何页面都先进登录页（除非已在登录页）
// 登录成功后 token 已存，后续路由切换正常放行
let _hasEnteredLoginOnce = false

router.beforeEach((to, from) => {
  // 已在登录页，不拦截（避免死循环）
  if (to.path === '/login') {
    _hasEnteredLoginOnce = true
    return true
  }

  // 先检查 token 有效性：有有效 token 直接放行，避免刷新页面时先闪到登录页再跳回
  const token = sessionStorage.getItem('token')
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      if (payload.exp && payload.exp * 1000 >= Date.now()) {
        // token 有效，直接放行
        _hasEnteredLoginOnce = true
        const role = sessionStorage.getItem('role') || 'user'
        if ((to.path === '/plugins' || to.path === '/mcp' || to.path === '/config' || to.path === '/dms') && role !== 'admin') {
          return '/'
        }
        return true
      } else {
        sessionStorage.removeItem('token')
        sessionStorage.removeItem('role')
        return '/login'
      }
    } catch {
      sessionStorage.removeItem('token')
      sessionStorage.removeItem('role')
      return '/login'
    }
  }

  // 无 token：强制去登录页
  return '/login'
})

export default router
