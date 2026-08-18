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
      { path: 'plugins', name: 'Plugins', component: () => import('./views/PluginsView.vue') },
      { path: 'document/:id', name: 'DocumentDetail', component: () => import('./views/DocumentDetail.vue') },
    ]
  },
  { path: '/login', name: 'Login', component: () => import('./views/LoginView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 路由守卫
router.beforeEach((to, from) => {
  const token = localStorage.getItem('token')
  const role = localStorage.getItem('role') || 'user'
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  // 管理员专属路由（插件管理）拦截普通用户
  if (to.path === '/plugins' && role !== 'admin') {
    return '/'
  }
  return true
})

export default router
