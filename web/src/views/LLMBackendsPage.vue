<template>
  <v-container>
    <h2 class="mb-1">LLM 后端注册表</h2>
    <p class="text-medium-emphasis mb-4">
      每个好友可绑定一个后端（ada1=gpt、ada2=deepseek）。
      <strong>密钥不在这里填明文</strong>——只填环境变量名（如 <code>DEEPSEEK_KEY</code>），真实 key 由运行环境提供，不入库、不进 git。
    </p>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <!-- 新建 / 编辑表单 -->
    <v-card variant="outlined" class="mb-6">
      <v-card-title>{{ editing ? '编辑' : '新建' }}后端</v-card-title>
      <v-card-text>
        <v-text-field v-model="form.id" label="id（唯一，好友按它绑定）" :disabled="editing" variant="outlined" />
        <v-text-field v-model="form.name" label="显示名（如 GPT-4o / DeepSeek-V3）" variant="outlined" />
        <v-text-field v-model="form.base_url" label="base_url（OpenAI 兼容 /v1）" variant="outlined" />
        <v-text-field
          v-model="form.api_key_env"
          label="api_key_env（环境变量名，非明文 key）"
          placeholder="DEEPSEEK_KEY"
          variant="outlined"
        />
        <v-text-field v-model="form.model" label="model（模型名）" variant="outlined" />
        <v-row>
          <v-col cols="6">
            <v-text-field v-model.number="form.temperature" label="temperature" type="number" step="0.05" variant="outlined" />
          </v-col>
          <v-col cols="6">
            <v-text-field v-model.number="form.max_tokens" label="max_tokens（留空=不限）" type="number" variant="outlined" />
          </v-col>
        </v-row>
        <v-btn color="primary" :loading="loading" :disabled="!form.id || !form.name" @click="save">
          {{ editing ? '保存' : '新建' }}
        </v-btn>
        <v-btn v-if="editing" class="ml-2" variant="text" @click="resetForm">取消</v-btn>
      </v-card-text>
    </v-card>

    <!-- 列表 -->
    <v-list>
      <v-list-item
        v-for="b in backends"
        :key="b.id"
        :title="`${b.name}（${b.id}）`"
        :subtitle="`model:${b.model || '—'} · base_url:${b.base_url || '—'} · key 来自环境变量:${b.api_key_env || '—'} · temp:${b.temperature}`"
      >
        <template #append>
          <v-btn size="small" variant="text" @click="edit(b)">编辑</v-btn>
          <v-btn size="small" color="error" variant="text" @click="remove(b.id)">删除</v-btn>
        </template>
      </v-list-item>
      <v-list-item v-if="!backends.length" title="还没有后端" subtitle="新建一个，再到「好友」页给好友绑定它。" />
    </v-list>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { createLlmBackend, deleteLlmBackend, listLlmBackends, updateLlmBackend } from '../api/chorus'

const backends = ref([])
const loading = ref(false)
const error = ref('')
const editing = ref(false)

const blank = () => ({ id: '', name: '', base_url: '', api_key_env: '', model: '', temperature: 0.75, max_tokens: null })
const form = ref(blank())

function resetForm() {
  form.value = blank()
  editing.value = false
}

async function load() {
  error.value = ''
  try {
    backends.value = await listLlmBackends()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function save() {
  loading.value = true
  error.value = ''
  try {
    // max_tokens 空字符串/NaN → null（后端 int|None）
    const payload = { ...form.value, max_tokens: form.value.max_tokens || null }
    if (editing.value) {
      await updateLlmBackend(form.value.id, payload)
    } else {
      await createLlmBackend(payload)
    }
    resetForm()
    await load()
  } catch (e) {
    error.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    loading.value = false
  }
}

function edit(b) {
  form.value = {
    id: b.id, name: b.name, base_url: b.base_url, api_key_env: b.api_key_env,
    model: b.model, temperature: b.temperature, max_tokens: b.max_tokens,
  }
  editing.value = true
}

async function remove(id) {
  error.value = ''
  try {
    await deleteLlmBackend(id)
    await load()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(load)
</script>
