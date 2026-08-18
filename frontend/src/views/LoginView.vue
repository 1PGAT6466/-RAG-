<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-title">伏羲 · 知识库</div>
      <div class="login-subtitle">工业文档管理与智能检索</div>
      <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleSubmit">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" :loading="loading" native-type="submit" style="width:100%">
            {{ isRegister ? '注册' : '登录' }}
          </el-button>
        </el-form-item>
      </el-form>
      <div style="text-align:center;font-size:13px;color:var(--text-tertiary);cursor:pointer" @click="isRegister = !isRegister">
        {{ isRegister ? '已有账号？登录' : '没有账号？注册' }}
      </div>
      <div v-if="error" style="text-align:center;color:#f56c6c;margin-top:12px;font-size:13px">{{ error }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

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
