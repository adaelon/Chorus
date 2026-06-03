<template>
  <div>
    <!-- 名称 + 保存（id 自动生成，用户不用管）-->
    <div class="d-flex align-center mb-3" style="gap: 10px; flex-wrap: wrap">
      <v-text-field
        v-model="name"
        label="配方名称"
        placeholder="例：先发散再投票"
        density="compact"
        variant="outlined"
        hide-details
        style="max-width: 280px"
      />
      <v-btn color="primary" :disabled="!canSave" :loading="saving" @click="save">保存</v-btn>
      <v-btn variant="text" @click="$emit('cancel')">取消</v-btn>
      <span v-if="!name.trim()" class="text-caption text-medium-emphasis">给配方起个名字才能保存</span>
    </div>

    <!-- 实时校验：整体问题留顶部，其余标在对应卡片上 -->
    <v-alert v-if="!errors.length" type="success" density="compact" variant="tonal" class="mb-3" text="图合法，可保存/运行" />
    <template v-else>
      <v-alert v-if="globalErrs.length" type="warning" density="compact" class="mb-2">
        <div class="text-subtitle-2 mb-1">整体问题：</div>
        <div v-for="(e, i) in globalErrs" :key="i" class="text-body-2">· {{ e }}</div>
      </v-alert>
      <div class="text-caption text-warning mb-3">共 {{ errors.length }} 个问题（其余已标在对应卡片上）。</div>
    </template>
    <v-alert v-if="saveErr" type="error" density="compact" class="mb-3" :text="saveErr" />

    <!-- 加卡：原语卡片库 -->
    <div class="mb-3">
      <span class="text-caption mr-2">加一张卡：</span>
      <v-chip
        v-for="p in palette"
        :key="p.name"
        size="small"
        class="mr-1 mb-1"
        label
        @click="addNode(p.name)"
      >
        ＋ {{ labelOf(p.name) }}
      </v-chip>
    </div>

    <!-- 开始 → 出边 -->
    <div class="endpoint">▷ 开始</div>
    <div class="edges-block mb-2">
      <div v-for="(e, i) in edgesFrom('START')" :key="i" class="edge-row">
        <span class="muted">→</span>
        <v-select
          :model-value="e.to"
          :items="targetItems"
          density="compact"
          variant="outlined"
          hide-details
          style="max-width: 200px"
          @update:model-value="(v) => (e.to = v)"
        />
        <v-btn size="x-small" icon variant="text" @click="delEdge(e)">✕</v-btn>
      </div>
      <v-btn size="x-small" variant="text" @click="addEdge('START')">＋ 开始出边</v-btn>
    </div>

    <!-- 每张卡 -->
    <template v-for="n in ordered" :key="n.id">
      <v-card variant="outlined" class="node mb-2" :class="[`k-${kindOf(n)}`, { 'has-err': nodeErrs(n.id).length }]">
        <div class="d-flex align-center">
          <span class="title">{{ labelOf(n.use) }}</span>
          <v-chip size="x-small" label class="ml-2" :color="kindColor(kindOf(n))">{{ kindZh(n) }}</v-chip>
          <span v-if="n.id !== n.use" class="node-id text-medium-emphasis ml-2">{{ n.id }}</span>
          <v-spacer />
          <v-btn size="x-small" icon variant="text" color="error" @click="delNode(n.id)">🗑</v-btn>
        </div>

        <!-- 出边编辑 -->
        <div class="edges-block mt-2">
          <div v-for="(e, i) in edgesFrom(n.id)" :key="i" class="edge-row">
            <v-select
              v-if="emitsOf(n).length"
              :model-value="condValue(e)"
              :items="condItems(n)"
              density="compact"
              variant="outlined"
              hide-details
              style="min-width: 150px; max-width: 200px"
              @update:model-value="(v) => setCond(e, v)"
            />
            <span v-else class="muted">→</span>
            <v-select
              :model-value="e.to"
              :items="targetItems"
              density="compact"
              variant="outlined"
              hide-details
              style="max-width: 200px"
              @update:model-value="(v) => (e.to = v)"
            />
            <v-btn size="x-small" icon variant="text" @click="delEdge(e)">✕</v-btn>
          </div>
          <v-btn size="x-small" variant="text" @click="addEdge(n.id)">＋ 出边</v-btn>
        </div>

        <!-- 本卡的校验问题 -->
        <div v-if="nodeErrs(n.id).length" class="node-errs">
          <div v-for="(e, i) in nodeErrs(n.id)" :key="i">⚠ {{ e }}</div>
        </div>
      </v-card>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { KIND_COLOR, KIND_ZH, decisionLabel, labelOf } from '../utils/recipeLabels'
import { createRecipe, updateRecipe, validateRecipe } from '../api/chorus'

const props = defineProps({
  initialGraph: { type: Object, required: true },
  initialName: { type: String, default: '' },
  recipeId: { type: String, default: '' }, // 非空=编辑已有自定义；空=新建/复制草稿
  primitives: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['saved', 'cancel'])

const clone = (o) => JSON.parse(JSON.stringify(o))
const graph = reactive(clone(props.initialGraph))
graph.nodes ||= []
graph.edges ||= []
const name = ref(props.initialName)
// id 是内部键：编辑已有用其 id；新建/复制草稿自动生成（用户只管名称）。
const rid = props.recipeId || `rcp-${crypto.randomUUID().slice(0, 8)}`
const errors = ref([])
const saving = ref(false)
const saveErr = ref('')

const ELSE = '__else__'
const palette = computed(() => Object.values(props.primitives))
const nodeById = computed(() => Object.fromEntries(graph.nodes.map((n) => [n.id, n])))
const kindOf = (n) => props.primitives[n.use]?.kind || ''
const kindZh = (n) => KIND_ZH[kindOf(n)] || '?'
const kindColor = (k) => KIND_COLOR[k] || undefined
const emitsOf = (n) => props.primitives[n.use]?.emits || []

// 目标下拉：所有节点（人话名）+ 结束。
const targetItems = computed(() => [
  ...graph.nodes.map((n) => ({ title: labelOf(n.use) + (n.id !== n.use ? `（${n.id}）` : ''), value: n.id })),
  { title: '结束', value: 'END' },
])

// 条件下拉（router/human）：否则 + 各 emits 人话。
const condItems = (n) => [
  { title: '否则（兜底）', value: ELSE },
  ...emitsOf(n).map((v) => ({ title: decisionLabel(v), value: v })),
]
const condValue = (e) =>
  e.when && e.when.field === 'next_decision' ? e.when.value : ELSE
const setCond = (e, v) => {
  if (v === ELSE) delete e.when
  else e.when = { field: 'next_decision', op: '==', value: v }
}

const edgesFrom = (id) => graph.edges.filter((e) => e.from === id)

// 把人话错误归到提及它的节点 id 上（id 以非标识符字符为边界，避免子串误匹配）。
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const errMatchesNode = (err, id) => new RegExp(`(^|[^\\w-])${escapeRe(id)}([^\\w-]|$)`).test(err)
const nodeErrs = (id) => errors.value.filter((e) => errMatchesNode(e, id))
const globalErrs = computed(() =>
  errors.value.filter((e) => !graph.nodes.some((n) => errMatchesNode(e, n.id))),
)

// DFS 前序排卡（与 RecipeFlow 一致）。
const ordered = computed(() => {
  const succ = {}
  for (const e of graph.edges) (succ[e.from] ||= []).push(e.to)
  const seen = new Set()
  const out = []
  const visit = (id) => {
    if (id === 'START' || id === 'END' || seen.has(id)) return
    seen.add(id)
    if (nodeById.value[id]) out.push(nodeById.value[id])
    for (const t of succ[id] || []) visit(t)
  }
  for (const t of succ['START'] || []) visit(t)
  for (const n of graph.nodes) if (!seen.has(n.id)) out.push(n)
  return out
})

function uniqueId(use) {
  if (!nodeById.value[use]) return use
  let i = 2
  while (nodeById.value[`${use}-${i}`]) i++
  return `${use}-${i}`
}
function addNode(use) {
  graph.nodes.push({ id: uniqueId(use), use })
}
function delNode(id) {
  graph.nodes = graph.nodes.filter((n) => n.id !== id)
  graph.edges = graph.edges.filter((e) => e.from !== id && e.to !== id)
}
function addEdge(from) {
  graph.edges.push({ from, to: 'END' })
}
function delEdge(edge) {
  const i = graph.edges.indexOf(edge)
  if (i >= 0) graph.edges.splice(i, 1)
}

// 实时校验（去抖）。
let timer = null
watch(
  graph,
  () => {
    clearTimeout(timer)
    timer = setTimeout(async () => {
      try {
        errors.value = await validateRecipe(payload().graph)
      } catch (e) {
        errors.value = ['校验请求失败：' + String(e?.message || e)]
      }
    }, 300)
  },
  { deep: true, immediate: true },
)

const canSave = computed(() => name.value.trim() && !errors.value.length && !saving.value)

function payload() {
  return {
    id: rid,
    name: name.value.trim(),
    graph: { ...graph, recipe: rid, version: graph.version || 1 },
  }
}

async function save() {
  saving.value = true
  saveErr.value = ''
  try {
    const p = payload()
    const r = props.recipeId ? await updateRecipe(props.recipeId, p) : await createRecipe(p)
    emit('saved', r)
  } catch (e) {
    const d = e?.response?.data?.detail
    saveErr.value = Array.isArray(d) ? d.join('；') : String(d || e?.message || e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.endpoint {
  font-size: 0.85rem;
  opacity: 0.7;
}
.node {
  padding: 8px 12px;
}
.node.k-router { border-left: 3px solid rgb(56, 142, 60); }
.node.k-human { border-left: 3px solid rgb(245, 124, 0); }
.node.k-transform { border-left: 3px solid rgb(25, 118, 210); }
.node.has-err {
  border-color: rgb(211, 47, 47);
  background: rgba(211, 47, 47, 0.04);
}
.node-errs {
  margin-top: 6px;
  border-top: 1px dashed rgba(211, 47, 47, 0.4);
  padding-top: 4px;
  font-size: 0.78rem;
  color: rgb(211, 47, 47);
}
.title { font-weight: 600; }
.node-id { font-size: 0.72rem; }
.edges-block { padding-left: 8px; }
.edge-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
}
.muted { opacity: 0.5; }
</style>
