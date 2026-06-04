<template>
  <v-container>
    <h2 class="mb-1">LLM 后端注册表</h2>
    <p class="text-medium-emphasis mb-4">
      好友的"模型来源"：openai 兼容后端（直接粘贴 key，存本地 DB、不进 git）或 AstrBot bot（整 bot=模型+通道）。
      <v-btn size="small" variant="tonal" class="ml-2" :loading="importing" @click="importBots">从 AstrBot 导入 bot</v-btn>
    </p>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />
    <v-alert v-if="importMsg" type="info" class="mb-4" density="compact" :text="importMsg" />

    <!-- 新建 / 编辑表单 -->
    <v-card variant="outlined" class="mb-6">
      <v-card-title>{{ editing ? '编辑' : '新建' }}后端</v-card-title>
      <v-card-text>
        <v-text-field v-model="form.name" label="显示名（如 GPT-4o / DeepSeek-V3；好友按它认）" variant="outlined" />
        <v-select
          v-model="form.kind"
          :items="[
            { value: 'openai', title: 'openai 兼容（独立，自填 base_url/key/model）' },
            { value: 'astrbot_bot', title: 'AstrBot bot（整 bot：模型+通道都用该 bot）' },
            { value: 'astrbot', title: 'astrbot provider（委托指定 provider，进阶）' },
          ]"
          item-title="title"
          item-value="value"
          label="后端类型"
          variant="outlined"
        />

        <!-- kind=openai：独立后端 -->
        <template v-if="form.kind === 'openai'">
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

        <!-- kind=astrbot_bot：整 bot（模型+通道都用该 bot） -->
        <template v-else-if="form.kind === 'astrbot_bot'">
          <v-text-field
            v-model="form.bot_id"
            label="bot_id（AstrBot platform 实例 id）"
            hint="好友选此后端 → 模型用该 bot 在用的 provider、出站以该 bot 身份发言。需 AstrBot 在跑。"
            persistent-hint
            variant="outlined"
          />
        </template>

        <!-- kind=astrbot：委托指定 provider（进阶） -->
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
        :subtitle="subtitleOf(b)"
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
  listAstrbotBots,
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
const importing = ref(false)
const importMsg = ref('')
const testResult = ref(null) // { ok, reply?, error? }
const modelOptions = ref([])

const blank = () => ({ id: '', name: '', kind: 'openai', base_url: '', api_key: '', model: '', temperature: 0.75, max_tokens: null, provider_id: '', bot_id: '' })
const form = ref(blank())

// 测试连通可用条件：openai 需 base_url/key/model；astrbot(provider) 需 provider_id；
// astrbot_bot 不支持（要群内 umo 才能取该 bot 的 provider，连通在圆桌发言时验证）。
const canTest = computed(() => {
  if (form.value.kind === 'astrbot_bot') return false
  if (form.value.kind === 'astrbot') return !!form.value.provider_id
  return !!(form.value.base_url && form.value.api_key && form.value.model)
})

function subtitleOf(b) {
  if (b.kind === 'astrbot_bot') return `类型:AstrBot bot · bot:${b.bot_id || '—'}（模型+通道）`
  if (b.kind === 'astrbot') return `类型:astrbot provider · 委托:${b.provider_id || '—'}`
  return `类型:openai · model:${b.model || '—'} · base_url:${b.base_url || '—'} · key:${b.api_key ? '已设' : '未设'} · temp:${b.temperature}`
}

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
    model: b.model, temperature: b.temperature, max_tokens: b.max_tokens,
    provider_id: b.provider_id || '', bot_id: b.bot_id || '',
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

// S7.4d：从桥拉 AstrBot bot 列表，为尚未登记的 bot_id 批量建 astrbot_bot 后端（按 bot_id 去重）。
async function importBots() {
  importing.value = true
  error.value = ''
  importMsg.value = ''
  try {
    const r = await listAstrbotBots()
    if (!r.ok) {
      error.value = `从 AstrBot 拉取失败：${r.error}（需 AstrBot + group_relay 桥在跑）`
      return
    }
    const have = new Set(backends.value.filter((b) => b.kind === 'astrbot_bot').map((b) => b.bot_id))
    let added = 0
    for (const bot of r.bots) {
      if (!bot.id || have.has(bot.id)) continue
      await createLlmBackend({
        id: `llm-${crypto.randomUUID().slice(0, 8)}`,
        name: `AstrBot:${bot.name || bot.id}`,
        kind: 'astrbot_bot',
        bot_id: bot.id,
      })
      added++
    }
    importMsg.value = `已导入 ${added} 个新 bot（共 ${r.bots.length} 个，已存在的跳过）`
    await load()
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>
