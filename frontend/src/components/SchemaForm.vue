<template>
  <el-form label-width="100px" label-position="left">
    <el-form-item
      v-for="key in orderedKeys"
      :key="key"
      :label="labelFor(key)"
    >
      <!-- string -->
      <el-input
        v-if="propType(key) === 'string' && (field(key).ui || '') === 'textarea'"
        v-model="model[key]"
        type="textarea"
        :rows="3"
      />
      <el-input
        v-else-if="propType(key) === 'string'"
        v-model="model[key]"
      />
      <!-- enum → select -->
      <el-select
        v-else-if="propType(key) === 'enum'"
        v-model="model[key]"
        style="width:100%"
      >
        <el-option
          v-for="opt in field(key).enum"
          :key="opt"
          :label="opt"
          :value="opt"
        />
      </el-select>
      <!-- number/integer -->
      <el-input-number
        v-else-if="propType(key) === 'integer' || propType(key) === 'number'"
        v-model="model[key]"
        style="width:100%"
      />
      <!-- boolean -->
      <el-switch v-else-if="propType(key) === 'boolean'" v-model="model[key]" />
      <!-- 默认文本 -->
      <el-input v-else v-model="model[key]" />
    </el-form-item>
  </el-form>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  modelValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue'])

// 内部可编辑模型（初始化为默认值）
const model = ref({})

function initModel() {
  const init = { ...props.modelValue }
  const props_ = props.schema?.properties || {}
  for (const [key, spec] of Object.entries(props_)) {
    if (init[key] === undefined && spec?.default !== undefined) {
      init[key] = spec.default
    }
  }
  model.value = init
}

initModel()

watch(() => props.modelValue, initModel)

watch(model, (v) => emit('update:modelValue', { ...v }), { deep: true })

const orderedKeys = computed(() => {
  const order = props.schema?.['ui:order'] || []
  const props_ = Object.keys(props.schema?.properties || {})
  // order 优先，其余按 properties 顺序补全
  return [...order, ...props_.filter(k => !order.includes(k))]
})

function field(key) {
  return props.schema?.properties?.[key] || {}
}

function propType(key) {
  const f = field(key)
  if (f.enum) return 'enum'
  return f.type || 'string'
}

function labelFor(key) {
  const f = field(key)
  return f.title || f.description || key
}
</script>
