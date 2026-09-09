<template>
  <div class="login-page">
    <!-- 左侧品牌区：工业精工品牌故事 -->
    <aside class="login-brand">
      <div class="login-brand-inner">
        <div class="login-brand-top">
          <div class="login-brand-mark"><span class="login-brand-glyph">伏</span></div>
          <div class="login-brand-name">伏羲 · 知识库</div>
        </div>

        <div class="login-brand-hero">
          <h1 class="login-brand-title">工业文档<br/>智能检索平台</h1>
          <p class="login-brand-desc">
            规整的文档源，精准的知识抽取，<br/>让每一次检索都直达答案。
          </p>
        </div>

        <ul class="login-brand-features">
          <li class="login-brand-feature">
            <span class="login-feature-icon"><el-icon><Search /></el-icon></span>
            <div>
              <div class="login-feature-title">混合检索</div>
              <div class="login-feature-desc">BM25 + 向量 + 图谱多重召回，融合排序</div>
            </div>
          </li>
          <li class="login-brand-feature">
            <span class="login-feature-icon"><el-icon><Connection /></el-icon></span>
            <div>
              <div class="login-feature-title">知识图谱</div>
              <div class="login-feature-desc">实体关系导航，连接器·材料·标准一键串联</div>
            </div>
          </li>
          <li class="login-brand-feature">
            <span class="login-feature-icon"><el-icon><Document /></el-icon></span>
            <div>
              <div class="login-feature-title">受控文档源</div>
              <div class="login-feature-desc">SeedDMS 规整仓储，清洗切片向量化入库</div>
            </div>
          </li>
        </ul>

        <div class="login-brand-foot">
          <span>FU XI · RAG PLATFORM</span>
          <span>v1.0.0</span>
        </div>
      </div>
    </aside>

    <!-- 右侧登录表单 -->
    <div class="login-form-side">
      <div class="login-card">
        <div class="login-card-head">
          <div class="login-card-title">欢迎回来</div>
          <div class="login-card-sub">登录以继续访问知识库</div>
        </div>

        <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleSubmit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" size="large">
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item prop="password">
            <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password>
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item style="margin-bottom:8px">
            <el-button type="primary" size="large" :loading="loading" native-type="submit" style="width:100%">
              {{ isRegister ? '注册' : '登录' }}
            </el-button>
          </el-form-item>
        </el-form>

        <div class="login-switch" @click="isRegister = !isRegister">
          {{ isRegister ? '已有账号？' : '没有账号？' }}<span class="login-switch-link">{{ isRegister ? '登录' : '注册' }}</span>
        </div>
        <div v-if="error" class="login-error">{{ error }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { User, Lock, Search, Connection, Document } from '@element-plus/icons-vue'

const router = useRouter()
const auth = useAuthStore()
const isRegister = ref(false)
const loading = ref(false)
const error = ref('')

const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, min: 6, message: '密码至少6位', trigger: 'blur' }]
}

async function handleSubmit() {
  loading.value = true
  error.value = ''
  try {
    if (isRegister.value) {
      await auth.register(form.username, form.password)
      await auth.login(form.username, form.password)
    } else {
      await auth.login(form.username, form.password)
    }
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  position: relative;
  background: var(--bg-secondary);
}

/* ==== 左侧品牌区：深青蓝渐变 + 工业网格 ==== */
.login-brand {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(ellipse 120% 100% at 0% 0%, rgba(255,255,255,0.06), transparent 50%),
    linear-gradient(150deg, #0a5a57 0%, #0e6e6a 45%, #0b4f4c 100%);
}
/* 工业网格纹理叠加 */
.login-brand::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px);
  background-size: 32px 32px;
  pointer-events: none;
}
/* 底部金色装饰线：更克制，半透明渐隐 */
.login-brand::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  background: linear-gradient(90deg, rgba(194,112,61,0.7), transparent 55%);
}

.login-brand-inner {
  position: relative;
  z-index: 1;
  max-width: 480px;
  width: 100%;
  display: flex;
  flex-direction: column;
  min-height: 520px;
}

.login-brand-top {
  display: flex;
  align-items: center;
  gap: 12px;
}
.login-brand-mark {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: rgba(255,255,255,0.14);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 16px rgba(0,0,0,0.18);
}
.login-brand-mark::after {
  content: '';
  position: absolute;
  right: -3px;
  bottom: -3px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--accent-warm);
  border: 2px solid #0e6e6a;
  opacity: 0.9;
}
.login-brand-glyph {
  font-size: 20px;
  font-weight: 700;
  color: #fff;
}
.login-brand-name {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1px;
}

.login-brand-hero {
  margin: 64px 0 48px;
}
.login-brand-title {
  font-size: 40px;
  line-height: 1.25;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1px;
  margin: 0 0 20px;
}
.login-brand-desc {
  font-size: 15px;
  line-height: 1.7;
  color: rgba(255,255,255,0.72);
  margin: 0;
}

.login-brand-features {
  list-style: none;
  margin: 0 0 64px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* 玻璃拟态特性卡片：毛玻璃 + 半透描边，悬停抬升 + 增强光晕 */
.login-brand-feature {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  border-radius: 14px;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.14);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1), background 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
  will-change: transform;
}
.login-brand-feature:hover {
  transform: translateY(-3px);
  background: rgba(255,255,255,0.12);
  border-color: rgba(255,255,255,0.28);
  box-shadow: 0 12px 32px rgba(0,0,0,0.28), 0 0 0 1px rgba(255,255,255,0.06) inset;
}
.login-brand-feature:active {
  transform: translateY(-1px);
}
.login-feature-icon {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.18);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-warm);
  font-size: 19px;
  flex-shrink: 0;
  transition: background 0.25s ease, color 0.25s ease, transform 0.25s ease;
}
.login-brand-feature:hover .login-feature-icon {
  background: rgba(194,112,61,0.22);
  color: #ffd9bd;
  transform: scale(1.06);
}
.login-feature-title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  margin-bottom: 3px;
  letter-spacing: 0.3px;
}
.login-feature-desc {
  font-size: 12.5px;
  color: rgba(255,255,255,0.6);
  line-height: 1.5;
  transition: color 0.25s ease;
}
.login-brand-feature:hover .login-feature-desc {
  color: rgba(255,255,255,0.8);
}

.login-brand-foot {
  margin-top: auto;
  padding-top: 22px;
  border-top: 1px solid rgba(255,255,255,0.14);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.5px;
  color: rgba(255,255,255,0.45);
}

/* ==== 右侧登录表单 ==== */
.login-form-side {
  flex: 1.618;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
  position: relative;
  overflow: hidden;
  background: linear-gradient(160deg, #eef3ee 0%, #e4ece5 100%);
}
/* 背后清晰的彩色光斑（不预模糊），供登录卡的 backdrop-filter 真正虚化 → 才有毛玻璃通透感 */
.login-form-side::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle 200px at 15% 16%, rgba(194, 112, 61, 0.62), transparent 70%),
    radial-gradient(circle 260px at 90% 82%, rgba(14, 110, 106, 0.55), transparent 70%),
    radial-gradient(circle 150px at 84% 20%, rgba(88, 141, 124, 0.52), transparent 70%),
    radial-gradient(circle 220px at 10% 88%, rgba(224, 166, 100, 0.48), transparent 70%);
}
.login-form-side > * {
  position: relative;
  z-index: 1;
}

.login-card {
  width: 420px;
  max-width: 100%;
  background: linear-gradient(150deg, rgba(255, 255, 255, 0.46), rgba(255, 255, 255, 0.22));
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border-radius: 28px;
  padding: 44px 40px 40px;
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow:
    0 28px 70px rgba(31, 45, 41, 0.18),
    0 4px 12px rgba(31, 45, 41, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.login-card-head {
  margin-bottom: 28px;
}
.login-card-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}
.login-card-sub {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* 登录表单输入框：小米式通透白 + 柔和聚焦（覆盖 Element Plus 默认蓝灰） */
.login-card {
  /* 从源头覆盖 Element Plus 的输入框底色变量，解决淡蓝紫 */
  --el-input-bg-color: rgba(255, 255, 255, 0.72);
  --el-fill-color-blank: rgba(255, 255, 255, 0.72);
  --el-input-border-color: transparent;
  --el-input-hover-border-color: transparent;
  --el-input-focus-border-color: transparent;
}
.login-card :deep(.el-input__wrapper) {
  border-radius: 12px;
  padding: 2px 14px;
  background: rgba(255, 255, 255, 0.72) !important;
  background-color: rgba(255, 255, 255, 0.72) !important;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.05) inset !important;
}
.login-card :deep(.el-input__wrapper:hover) {
  background: rgba(255, 255, 255, 0.9) !important;
  background-color: rgba(255, 255, 255, 0.9) !important;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.12) inset !important;
}
.login-card :deep(.el-input__wrapper.is-focus) {
  background: #ffffff !important;
  background-color: #ffffff !important;
  box-shadow: 0 0 0 2px rgba(14, 110, 106, 0.35) inset, 0 0 0 4px rgba(14, 110, 106, 0.08) !important;
}
.login-card :deep(.el-input__inner) {
  height: 46px;
  font-size: 14px;
  background: transparent !important;
  color: #1f2d29;
}
.login-card :deep(.el-input__prefix),
.login-card :deep(.el-input__suffix) {
  color: #8a9791;
  background: transparent !important;
}
.login-card :deep(.el-button--primary) {
  border-radius: 12px;
  height: 48px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
  background: linear-gradient(135deg, #13a89e 0%, #0e6e6a 100%);
  border: none;
  box-shadow: 0 8px 22px rgba(14, 110, 106, 0.3);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.login-card :deep(.el-button--primary:hover) {
  transform: translateY(-1px);
  box-shadow: 0 12px 28px rgba(14, 110, 106, 0.38);
}
.login-card :deep(.el-button--primary:active) {
  transform: translateY(0);
}

.login-switch {
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary);
  cursor: pointer;
  user-select: none;
}
.login-switch-link {
  color: var(--accent);
  font-weight: 600;
  margin-left: 2px;
  transition: color var(--duration-fast);
}
.login-switch:hover .login-switch-link {
  color: var(--accent-hover);
  text-decoration: underline;
}

.login-error {
  text-align: center;
  color: var(--color-danger);
  margin-top: 12px;
  font-size: 13px;
}

/* 窄屏适配：隐藏品牌区，只留表单 */
@media (max-width: 860px) {
  .login-brand { display: none; }
}
</style>
