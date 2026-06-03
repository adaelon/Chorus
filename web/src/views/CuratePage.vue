<template>
  <v-container>
    <h2 class="mb-4">扇出策展（CuratePage）</h2>

    <!-- 需求 + 占位 roster -->
    <v-textarea v-model="request" label="需求" rows="2" auto-grow variant="outlined" />
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
      :disabled="!request || !selectedContacts.length"
      @click="runInbound"
    >
      并行生成候选
    </v-btn>
    <span v-if="status" class="text-caption ml-3">{{ status }}</span>
    <span v-else class="text-caption ml-3">（流式，token 边生成边出）</span>

    <v-alert v-if="error" type="error" class="mt-4" :text="error" />

    <!-- N 候选并排 -->
    <div v-if="candidates.length" class="mt-6">
      <h3 class="mb-2">候选（{{ candidates.length }}）</h3>
      <v-row>
        <v-col v-for="(c, i) in candidates" :key="`${c.contact_id}-${i}`" cols="12" md="4">
          <v-card variant="outlined" height="100%">
            <v-card-title>{{ c.contact_id }}</v-card-title>
            <v-card-subtitle v-if="c.dimension">{{ c.dimension }}</v-card-subtitle>
            <v-card-text class="md-body" v-html="renderMd(c.text)" />
            <v-card-actions>
              <v-btn size="small" @click="pick(c.contact_id)">选中</v-btn>
              <v-btn size="small" color="error" @click="eliminate(c.contact_id)">淘汰</v-btn>
            </v-card-actions>
          </v-card>
        </v-col>
      </v-row>

      <!-- 跨搬运 reassign -->
      <v-card variant="tonal" class="mt-4">
        <v-card-title>跨搬运（reassign：A 的点交 B 写）</v-card-title>
        <v-card-text>
          <v-text-field v-model="reassignPoint" label="要落地的点" variant="outlined" />
          <v-select v-model="reassignExecutor" :items="contactIds" label="交给谁写" variant="outlined" />
          <v-btn :disabled="!reassignPoint || !reassignExecutor" :loading="loading" @click="reassign">
            交办
          </v-btn>
        </v-card-text>
      </v-card>
    </div>

    <!-- picked -->
    <div v-if="picked.length" class="mt-6">
      <h3 class="mb-2">已选（picked）</h3>
      <v-list>
        <v-list-item v-for="(p, i) in picked" :key="i" :title="p.contact_id" :subtitle="p.text" />
      </v-list>
    </div>

    <!-- synthesize -->
    <div v-if="candidates.length" class="mt-6">
      <v-btn color="success" :loading="loading" @click="runSynthesize">汇总产出</v-btn>
      <v-card v-if="output" variant="outlined" class="mt-4">
        <v-card-title>产出</v-card-title>
        <v-card-text class="md-body" v-html="renderMd(output)" />
      </v-card>
    </div>
  </v-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { curate, inboundStream, listContacts, synthesize } from '../api/chorus'
import { renderMd } from '../utils/markdown'

const route = useRoute()
// 主持人荐配方带过来的任务（?task=）优先回填，否则用默认需求占位
const request = ref(route.query.task || '便利店要不要在春节期间继续营业')
const contactItems = ref([]) // {title, value:id}
const selectedContacts = ref([])
const groupKey = ref('')
const candidates = ref([])
const picked = ref([])
const output = ref('')
const loading = ref(false)
const error = ref('')
const status = ref('')

const reassignPoint = ref('')
const reassignExecutor = ref('')

const contactIds = computed(() => candidates.value.map((c) => c.contact_id))

async function loadContacts() {
  try {
    const cs = await listContacts()
    contactItems.value = cs.map((c) => ({ title: `${c.name}（${c.id}）`, value: c.id }))
  } catch (e) {
    error.value = String(e?.message || e)
  }
}

onMounted(loadContacts)

async function runInbound() {
  loading.value = true
  error.value = ''
  picked.value = []
  output.value = ''
  candidates.value = []
  status.value = ''
  const byId = {}
  groupKey.value = crypto.randomUUID()
  try {
    await inboundStream(groupKey.value, request.value, selectedContacts.value, {
      status: (e) => {
        status.value = e.stage === 'framing' ? '主持人分配维度中…' : e.stage
      },
      framed: (e) => {
        status.value = ''
        candidates.value = e.roster.map((r) => ({
          contact_id: r.contact_id,
          dimension: r.dimension,
          text: '',
        }))
        for (const c of candidates.value) byId[c.contact_id] = c
      },
      delta: (e) => {
        const c = byId[e.contact_id]
        if (c) c.text += e.text // 逐 token 追加到对应卡片
      },
      candidates: (e) => {
        candidates.value = e.candidates // 最终权威版
      },
      error: (e) => {
        error.value = e.detail
      },
    })
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
    status.value = ''
  }
}

async function applyCurate(commands) {
  loading.value = true
  error.value = ''
  try {
    const data = await curate(groupKey.value, commands)
    candidates.value = data.candidates
    picked.value = data.picked
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

const pick = (contact_id) => applyCurate([{ kind: 'pick', contact_id }])
const eliminate = (contact_id) => applyCurate([{ kind: 'eliminate', contact_id }])

async function reassign() {
  await applyCurate([
    { kind: 'reassign', point: reassignPoint.value, executor_id: reassignExecutor.value },
  ])
  reassignPoint.value = ''
  reassignExecutor.value = ''
}

async function runSynthesize() {
  loading.value = true
  error.value = ''
  try {
    const data = await synthesize(groupKey.value)
    output.value = data.output
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* v-html 注入的内容需 :deep() 才能被 scoped 样式命中 */
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0.6em 0 0.3em;
}
.md-body :deep(p) {
  margin: 0.4em 0;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 1.4em;
  margin: 0.4em 0;
}
.md-body :deep(code) {
  background: rgba(127, 127, 127, 0.15);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
.md-body :deep(pre) {
  background: rgba(127, 127, 127, 0.12);
  padding: 0.6em;
  border-radius: 6px;
  overflow-x: auto;
}
.md-body :deep(blockquote) {
  border-left: 3px solid rgba(127, 127, 127, 0.4);
  padding-left: 0.8em;
  margin: 0.4em 0;
  opacity: 0.85;
}
</style>
