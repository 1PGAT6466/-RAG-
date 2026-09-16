import { defineStore } from 'pinia'
import api from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: sessionStorage.getItem('token') || '',
    username: sessionStorage.getItem('username') || '',
    role: sessionStorage.getItem('role') || 'user',
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    isAdmin: (state) => state.role === 'admin',
  },
  actions: {
    async login(username, password) {
      const { data } = await api.post('/auth/login', { username, password })
      this.token = data.data.token
      this.username = data.data.username
      this.role = data.data.role || 'user'
      sessionStorage.setItem('token', this.token)
      sessionStorage.setItem('username', this.username)
      sessionStorage.setItem('role', this.role)
    },
    async register(username, password) {
      await api.post('/auth/register', { username, password })
    },
    logout() {
      this.token = ''
      this.username = ''
      this.role = 'user'
      sessionStorage.removeItem('token')
      sessionStorage.removeItem('username')
      sessionStorage.removeItem('role')
    }
  }
})
