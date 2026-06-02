<template>
  <v-container>
    <h2 class="mb-4">圆桌（ChatPage）</h2>

    <!-- 议题 + 到场好友 -->
    <v-textarea v-model="topic" label="圆桌议题" rows="2" auto-grow variant="outlined" />
    <v-select
      v-model="selectedContacts"
      :items="contactItems"
      label="到场好友（从注册表选）"
      multiple
      chips
      variant="outlined"
      :hint="contactItems.length ? '' : '注册表为空——先去“好友”页新建'"
      persistent-hint
    />
    <v-btn
      color="primary"
      :loading="loading"
      :disabled="!topic || !selectedContacts.length || loading"
      @click="start"
    >
      开始圆桌
    </v-btn>
    <span v-if="status" class="text-caption ml-3">{{ status }}</span>

    <v-alert v-if="error" type="error" class="mt-4" :text="error" />

    <!-- 群视图：多身份气泡 -->
    <div v-if="messages.length" class="mt-6 chat">
      <div
        v-for="(m, i) in messages"
        :key="i"
        class="bubble-row"
        :class="m.sender_kind === 'human' ? 'right' : 'left'"
      >
        <v-avatar
          v-if="m.sender_kind !== 'human'"
          :color="avatarColor(m.sender_id)"
          size="36"
          class="avatar"
        >
          <span class="text-caption">{{ initial(m.sender_id) }}</span>
        </v-avatar>
        <div class="bubble" :class="bubbleClass(m)">
          <div class="bubble-head">
            <span class="name">{{ nameOf(m.sender_id, m.sender_kind) }}</span>
            <v-chip v-if="m.dimension" size="x-small" class="ml-2" label>{{ m.dimension }}</v-chip>
          </div>
          <!-- 澄清问气泡：可答/跳过 -->
          <template v-if="m.kind === 'clarify'">
            <div class="md-body" v-html="renderMd(m.text)" />
            <div v-if="paused && pauseType === 'clarify'" class="mt-2">
              <v-text-field
                v-model="clarifyAnswer"
                label="回答澄清问（或跳过）"
                density="compact"
                variant="outlined"
                hide-details
                @keyup.enter="answerClarify"
              />
              <div class="mt-2">
                <v-btn size="small" color="primary" :disabled="!clarifyAnswer" @click="answerClarify">
                  回答
                </v-btn>
                <v-btn size="small" variant="text" class="ml-2" @click="skipClarify">跳过</v-btn>
              </div>
            </div>
          </template>
          <div v-else class="md-body" v-html="renderMd(m.text || '…')" />
        </div>
      </div>
    </div>

    <!-- 插话窗口：每轮发言后在 human_gate 暂停 -->
    <v-card v-if="paused && pauseType === 'human_gate'" variant="tonal" class="mt-4">
      <v-card-text>
        <div class="text-caption mb-2">轮到你——可插话改向，或让讨论继续。</div>
        <v-text-field
          v-model="interjectText"
          label="插话（留空=继续讨论）"
          density="compact"
          variant="outlined"
          hide-details
          @keyup.enter="interjectAndResume"
        />
        <div class="mt-2">
          <v-btn size="small" color="primary" :disabled="!interjectText" @click="interjectAndResume">
            插话
          </v-btn>
          <v-btn size="small" variant="text" class="ml-2" :loading="loading" @click="continueDiscussion">
            继续
          </v-btn>
        </div>
      </v-card-text>
    </v-card>

    <!-- 圆桌主笔综合 -->
    <v-card v-if="output" variant="outlined" class="mt-4">
      <v-card-title>圆桌综合</v-card-title>
      <v-card-text class="md-body" v-html="renderMd(output)" />
    </v-card>
  </v-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listContacts, roundtableResume, roundtableStream } from '../api/chorus'
import { renderMd } from '../utils/markdown'

const topic = ref('要不要给便利店做付费会员')
const contactItems = ref([]) // {title, value:id}
const contactNames = ref({}) // id -> name
const selectedContacts = ref([])
const groupKey = ref('')

const messages = ref([]) // {sender_id, sender_kind, text, dimension?, kind?, streaming?}
const dims = ref({}) // contact_id -> dimension（FRAME 分配）
const output = ref('')
const loading = ref(false)
const error = ref('')
const status = ref('')

const paused = ref(false)
const pauseType = ref(null) // 'human_gate' | 'clarify'
const interjectText = ref('')
const clarifyAnswer = ref('')

let current = null // 当前正在流式追加的 ai 气泡

const PALETTE = ['#1976D2', '#388E3C', '#D32F2F', '#7B1FA2', '#F57C00', '#0097A7']
const avatarColor = (id) => PALETTE[hash(id) % PALETTE.length]
const initial = (id) => (id ? String(id).slice(0, 2).toUpperCase() : '?')
const nameOf = (id, kind) =>
  kind === 'human' ? '你' : kind === 'moderator' ? '主持人' : contactNames.value[id] || id
const bubbleClass = (m) =>
  m.sender_kind === 'human' ? 'human' : m.kind === 'clarify' ? 'clarify' : 'ai'

function hash(s) {
  let h = 0
  for (const ch of String(s)) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return h
}

async function loadContacts() {
  try {
    const cs = await listContacts()
    contactItems.value = cs.map((c) => ({ title: `${c.name}（${c.id}）`, value: c.id }))
    contactNames.value = Object.fromEntries(cs.map((c) => [c.id, c.name]))
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(loadContacts)

// SSE 事件 → 气泡。起场与续场共用同一套 handlers。
const handlers = {
  framed: (e) => {
    status.value = ''
    dims.value = Object.fromEntries(e.roster.map((r) => [r.contact_id, r.dimension]))
  },
  delta: (e) => {
    if (!e.contact_id) return // 防御：无归属的 token（非发言）不建气泡
    // 逐 token：找/建当前发言者的流式气泡
    if (!current || current.sender_id !== e.contact_id) {
      current = {
        sender_id: e.contact_id,
        sender_kind: 'ai',
        text: '',
        dimension: dims.value[e.contact_id],
        streaming: true,
      }
      messages.value.push(current)
    }
    current.text += e.text
  },
  turn: (e) => {
    // 一轮发言完成：落权威文本（无 delta 的离线/快路径则在此建气泡）
    if (current && current.sender_id === e.contact_id) {
      current.text = e.text
      current.streaming = false
    } else {
      messages.value.push({
        sender_id: e.contact_id,
        sender_kind: 'ai',
        text: e.text,
        dimension: e.dimension || dims.value[e.contact_id],
      })
    }
    current = null
  },
  clarify: (e) => {
    paused.value = true
    pauseType.value = 'clarify'
    const head = e.restate ? `${e.restate}\n\n` : ''
    messages.value.push({
      sender_id: 'moderator',
      sender_kind: 'moderator',
      kind: 'clarify',
      text: `${head}${e.question || ''}`,
    })
  },
  human_gate: () => {
    paused.value = true
    pauseType.value = 'human_gate'
  },
  output: (e) => {
    output.value = e.output
    paused.value = false
    pauseType.value = null
  },
  error: (e) => {
    error.value = e.detail
  },
}

async function runLeg(streamFn) {
  loading.value = true
  error.value = ''
  paused.value = false
  pauseType.value = null
  try {
    await streamFn()
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
    status.value = ''
  }
}

async function start() {
  groupKey.value = crypto.randomUUID()
  messages.value = [{ sender_id: 'you', sender_kind: 'human', text: topic.value }]
  dims.value = {}
  output.value = ''
  current = null
  status.value = '主持人分配维度中…'
  await runLeg(() =>
    roundtableStream(groupKey.value, topic.value, selectedContacts.value, handlers),
  )
}

const continueDiscussion = () =>
  runLeg(() => roundtableResume(groupKey.value, { interject: null }, handlers))

async function interjectAndResume() {
  if (!interjectText.value) return
  const text = interjectText.value
  interjectText.value = ''
  messages.value.push({ sender_id: 'you', sender_kind: 'human', text })
  await runLeg(() => roundtableResume(groupKey.value, { interject: text }, handlers))
}

async function answerClarify() {
  if (!clarifyAnswer.value) return
  const text = clarifyAnswer.value
  clarifyAnswer.value = ''
  messages.value.push({ sender_id: 'you', sender_kind: 'human', text })
  await runLeg(() => roundtableResume(groupKey.value, { answer: text }, handlers))
}

const skipClarify = () =>
  runLeg(() => roundtableResume(groupKey.value, { skip: true }, handlers))
</script>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bubble-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.bubble-row.right {
  flex-direction: row-reverse;
}
.bubble {
  max-width: 78%;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(127, 127, 127, 0.1);
}
.bubble.human {
  background: rgba(25, 118, 210, 0.15);
}
.bubble.clarify {
  background: rgba(245, 124, 0, 0.14);
}
.bubble-head {
  display: flex;
  align-items: center;
  margin-bottom: 2px;
}
.bubble-head .name {
  font-weight: 600;
  font-size: 0.85rem;
}
.avatar {
  flex: 0 0 auto;
}
.md-body :deep(p) {
  margin: 0.3em 0;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 1.3em;
  margin: 0.3em 0;
}
.md-body :deep(code) {
  background: rgba(127, 127, 127, 0.15);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
</style>
