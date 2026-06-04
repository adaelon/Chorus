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
        <v-text-field v-model="form.name" label="显示名（如 GPT-4o / DeepSeek-V3；好友按它认）" variant="outlined" />
        <v-select
          v-model="form.kind"
          :items="[
            { value: 'openai', title: 'openai 兼容（独立，自填 base_url/key/model）' },
            { value: 'astrbot', title: 'astrbot（委托给 AstrBot 已配好的 provider）' },
          ]"
          item-title="title"
          item-value="value"
          label="后端类型"
          variant="outlined"
        />

        <!-- kind=openai：独立后端 -->
        <template v-if="form.kind !== 'astrbot'">
          <v-text-field v-model="form.base_url" label="base_url（OpenAI 兼容 /v1）" variant="outlined" />
          <v-text-field
            v-model="form.api_key"
            label="API Key（直接粘贴你的 key）"
            placeholder="sk-..."
            type="password"
            variant="outlined"
          />
          <v-combobox
            v-model="form.model"
            :items="modelOptions"
            label="model（模型名；可手填或点「拉取模型」选）"
            variant="outlined"
            :loading="probing"
          >
            <template #append-inner>
              <v-btn size="small" variant="text" :disabled="!form.base_url || !form.api_key" @click="probe">拉取模型</v-btn>
            </template>
          </v-combobox>
          <v-row>
            <v-col cols="6">
              <v-text-field v-model.number="form.temperature" label="temperature" type="number" step="0.05" variant="outlined" />
            </v-col>
            <v-col cols="6">
              <v-text-field v-model.number="form.max_tokens" label="max_tokens（留空=不限）" type="number" variant="outlined" />
            </v-col>
          </v-row>
        </template>

        <!-- kind=astrbot：委托后端 -->
        <template v-else>
          <v-text-field
            v-model="form.provider_id"
            label="provider_id（AstrBot 里已配好的 provider id）"
            hint="key/模型/校验都在 AstrBot 那边；这里只引用。需 group_relay 桥在跑。"
            persistent-hint
            variant="outlined"
          />
        </template>
        <v-btn color="primary" :loading="loading" :disabled="!form.name" @click="save">
          {{ editing ? '保存' : '新建' }}
        </v-btn>
        <v-btn class="ml-2" variant="tonal" :loading="testing" :disabled="!canTest" @click="test">
          测试连通
        </v-btn>
        <v-btn v-if="editing" class="ml-2" variant="text" @click="resetForm">取消</v-btn>

        <v-alert
          v-if="testResult"
          :type="testResult.ok ? 'success' : 'error'"
          class="mt-4"
          density="compact"
          :text="testResult.ok ? `连通正常，回包：${testResult.reply || '（空）'}` : `连通失败：${testResult.error}`"
        />
      </v-card-text>
    </v-card>

    <!-- 列表 -->
    <v-list>
      <v-list-item
        v-for="b in backends"
        :key="b.id"
        :title="b.name"
        :subtitle="
          b.kind === 'astrbot'
            ? `类型:astrbot · 委托 provider:${b.provider_id || '—'}`
            : `类型:openai · model:${b.model || '—'} · base_url:${b.base_url || '—'} · key:${b.api_key ? '已设' : '未设'} · temp:${b.temperature}`
        "
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
import { computed, onMounted, ref } from 'vue'
import {
  createLlmBackend,
  deleteLlmBackend,
  listLlmBackends,
  probeLlmModels,
  testLlmBackend,
  updateLlmBackend,
} from '../api/chorus'

const backends = ref([])
const loading = ref(false)
const error = ref('')
const editing = ref(false)
const testing = ref(false)
const probing = ref(false)
const testResult = ref(null) // { ok, reply?, error? }
const modelOptions = ref([])

const blank = () => ({ id: '', name: '', kind: 'openai', base_url: '', api_key: '', model: '', temperature: 0.75, max_tokens: null, provider_id: '' })
const form = ref(blank())

// 测试连通可用条件：openai 需 base_url/key/model 齐；astrbot 需 provider_id。
const canTest = computed(() =>
  form.value.kind === 'astrbot'
    ? !!form.value.provider_id
    : !!(form.value.base_url && form.value.api_key && form.value.model),
)

function resetForm() {
  form.value = blank()
  editing.value = false
  testResult.value = null
  modelOptions.value = []
}

// S7.1d：测试连通（解析 env key 真打一次 ping）。配置对错由此立判。
async function test() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testLlmBackend(form.value)
  } catch (e) {
    testResult.value = { ok: false, error: String(e?.message || e) }
  } finally {
    testing.value = false
  }
}

// S7.1d：拉模型列表（GET {base_url}/v1/models）。拉到填进下拉，拉不到给提示、回退手填。
async function probe() {
  probing.value = true
  error.value = ''
  try {
    const r = await probeLlmModels({ base_url: form.value.base_url, api_key: form.value.api_key })
    if (r.ok) {
      modelOptions.value = r.models
      if (!r.models.length) error.value = '该后端没返回模型列表，请手填 model'
    } else {
      error.value = `拉取模型失败：${r.error}（可手填 model）`
    }
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    probing.value = false
  }
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
      payload.id = `llm-${crypto.randomUUID().slice(0, 8)}` // id 自动生成（用户只填 name），仿配方
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
    id: b.id, name: b.name, kind: b.kind || 'openai', base_url: b.base_url, api_key: b.api_key || '',
    model: b.model, temperature: b.temperature, max_tokens: b.max_tokens, provider_id: b.provider_id || '',
  }
  editing.value = true
  testResult.value = null
  modelOptions.value = b.model ? [b.model] : []
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
