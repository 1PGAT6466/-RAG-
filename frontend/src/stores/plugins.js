import { defineStore } from 'pinia'
import pluginsApi from '../api/plugins'

export const usePluginsStore = defineStore('plugins', {
  state: () => ({
    plugins: [],       // 插件列表
    loading: false,
  }),
  getters: {
    enabledPlugins: (state) => state.plugins.filter(p => p.status === 'enabled'),
  },
  actions: {
    async fetchList() {
      this.loading = true
      try {
        const { data } = await pluginsApi.list()
        this.plugins = data.data || []
      } finally {
        this.loading = false
      }
    },
    async enable(name) {
      await pluginsApi.enable(name)
      await this.fetchList()
    },
    async disable(name) {
      await pluginsApi.disable(name)
      await this.fetchList()
    },
    async uninstall(name) {
      await pluginsApi.uninstall(name)
      await this.fetchList()
    },
    async invoke(name, method, params) {
      const { data } = await pluginsApi.invoke(name, method, params)
      return data // { status, data }
    }
  }
})
