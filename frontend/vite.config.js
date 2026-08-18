import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    emptyOutDir: true
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://127.0.0.1:8099'
    }
  },
  resolve: {
    alias: { '@': '/src' }
  }
})
