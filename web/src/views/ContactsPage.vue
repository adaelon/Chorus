<template>
  <v-container>
    <h2 class="mb-4">好友注册表（Contact）</h2>

    <v-alert v-if="error" type="error" class="mb-4" :text="error" />

    <!-- 新建 / 编辑表单 -->
    <v-card variant="outlined" class="mb-6">
      <v-card-title>{{ editing ? '编辑' : '新建' }}好友</v-card-title>
      <v-card-text>
        <v-text-field v-model="form.id" label="id（唯一）" :disabled="editing" variant="outlined" />
        <v-text-field v-model="form.name" label="名字" variant="outlined" />
        <v-text-field v-model="form.title" label="头衔" variant="outlined" />
        <v-text-field v-model="form.persona_style" label="说话风格" variant="outlined" />
        <v-text-field v-model="form.base_stance" label="底层立场" variant="outlined" />
        <v-text-field
          v-model="form.bot_ref"
          label="bot_ref（AstrBot platform 实例 id，出站以该 bot 发言）"
          variant="outlined"
        />
        <v-select
          v-model="form.llm_ref"
          :items="backendItems"
          item-title="title"
          item-value="value"
          label="LLM 后端（该好友用哪个模型；留空=全局默认）"
          variant="outlined"
          clearable
          :hint="llmHint"
          persistent-hint
        />
        <v-btn color="primary" :loading="loading" :disabled="!form.id || !form.name" @click="save">
          {{ editing ? '保存' : '新建' }}
        </v-btn>
        <v-btn v-if="editing" class="ml-2" variant="text" @click="resetForm">取消</v-btn>
      </v-card-text>
    </v-card>

    <!-- 列表 -->
    <v-list>
      <v-list-item
        v-for="c in contacts"
        :key="c.id"
        :title="`${c.name}（${c.id}）`"
        :subtitle="`${c.title || '—'} · 风格:${c.persona_style || '—'} · 立场:${c.base_stance || '—'} · bot:${c.bot_ref || '—'} · 模型:${backendName(c.llm_ref)} · 信誉:${c.reputation}`"
      >
        <template #append>
          <v-btn size="small" variant="text" @click="edit(c)">编辑</v-btn>
          <v-btn size="small" color="error" variant="text" @click="remove(c.id)">删除</v-btn>
        </template>
      </v-list-item>
    </v-list>
  </v-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { createContact, deleteContact, listContacts, listLlmBackends, updateContact } from '../api/chorus'

const contacts = ref([])
const backends = ref([])
const loading = ref(false)
const error = ref('')
const editing = ref(false)

const FOLLOW_BOT = '@bot' // 与后端 FOLLOW_BOT_LLM_REF 一致（S7.3b 整 bot 引用）

const blank = () => ({ id: '', name: '', title: '', persona_style: '', base_stance: '', bot_ref: '', llm_ref: '' })
const form = ref(blank())

// 下拉项：「跟随我的 AstrBot bot」+ 各 LLM 后端（id → "显示名（model）"）
const backendItems = computed(() => [
  { value: FOLLOW_BOT, title: '跟随我的 AstrBot bot（bot_ref）' },
  ...backends.value.map((b) => ({ value: b.id, title: `${b.name}（${b.model || b.id}）` })),
])
function backendName(ref) {
  if (!ref) return '默认'
  if (ref === FOLLOW_BOT) return '跟随 bot'
  const b = backends.value.find((x) => x.id === ref)
  return b ? b.name : ref // 后端已删则显示原始 id
}
// 提示：选了跟随 bot 却没填 bot_ref → 会回退全局
const llmHint = computed(() => {
  if (form.value.llm_ref === FOLLOW_BOT)
    return form.value.bot_ref ? '模型与通道都用该 bot（需 AstrBot 在跑）' : '⚠ 选了跟随 bot，但未填 bot_ref，会回退全局默认'
  return backends.value.length ? '' : '还没有后端，去「模型」页新建一个'
})

function resetForm() {
  form.value = blank()
  editing.value = false
}

async function load() {
  error.value = ''
  try {
    ;[contacts.value, backends.value] = await Promise.all([listContacts(), listLlmBackends()])
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

async function save() {
  loading.value = true
  error.value = ''
  try {
    // clearable v-select 清空会置 null；后端 llm_ref 是 str，归一为 ''
    const payload = { ...form.value, llm_ref: form.value.llm_ref || '' }
    if (editing.value) {
      await updateContact(form.value.id, payload)
    } else {
      await createContact(payload)
    }
    resetForm()
    await load()
  } catch (e) {
    error.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    loading.value = false
  }
}

function edit(c) {
  form.value = { id: c.id, name: c.name, title: c.title, persona_style: c.persona_style, base_stance: c.base_stance, bot_ref: c.bot_ref || '', llm_ref: c.llm_ref || '' }
  editing.value = true
}

async function remove(id) {
  error.value = ''
  try {
    await deleteContact(id)
    await load()
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(load)
</script>
