import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  Aim, Back, ChatDotRound, CircleClose, Connection, Delete, Document, Download,
  Folder, Grid, Loading, Lock, MagicStick, MoreFilled, Plus, Promotion, Refresh,
  Search, Setting, Share, Shop, SwitchButton, TopRight, Upload, User,
} from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './styles/global.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// Element Plus 组件已通过 unplugin-vue-components 按需自动引入（见 vite.config.js），
// 此处不再 app.use(ElementPlus) 全量注册，也不全量引入样式。
// 命令式 API（ElMessage 等）由 unplugin-auto-import 自动引入 + 样式。

// 按需注册全局图标（全站实际用到 22 个，替代原先的全量 250+ 注册）
const icons = {
  Aim, Back, ChatDotRound, CircleClose, Connection, Delete, Document, Download,
  Folder, Grid, Loading, Lock, MagicStick, MoreFilled, Plus, Promotion, Refresh,
  Search, Setting, Share, Shop, SwitchButton, TopRight, Upload, User,
}
for (const [key, component] of Object.entries(icons)) {
  app.component(key, component)
}

app.mount('#app')
