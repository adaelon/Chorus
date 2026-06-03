<template>
  <div class="flow">
    <div class="endpoint">▷ 开始</div>
    <template v-for="(n, i) in ordered" :key="n.id">
      <div class="conn">↓</div>
      <v-card variant="outlined" class="node" :class="kindClass(n)">
        <div class="node-head">
          <span class="title">{{ label(n) }}</span>
          <v-chip size="x-small" label class="ml-2" :color="kindColor(n)">{{ kindZh(n) }}</v-chip>
          <v-chip v-if="budgetOf(n)" size="x-small" label class="ml-1" color="warning" variant="tonal">
            闸：{{ budgetOf(n) }}
          </v-chip>
        </div>
        <!-- 仅当实例 id 与原语名不同（同一原语用多次）才显示，避免 "frame · frame" 噪声 -->
        <div v-if="n.id !== n.use" class="node-id text-medium-emphasis">{{ n.use }} · {{ n.id }}</div>
        <!-- 出边：条件分支 / 循环回边，逐条人话标注 -->
        <div v-if="branchesOf(n.id).length" class="branches">
          <div v-for="(b, j) in branchesOf(n.id)" :key="j" class="branch" :class="{ back: b.back }">
            <span v-if="b.back" class="loop">↻ 循环</span>
            <span v-if="b.cond" class="cond">当 {{ b.cond }}</span>
            <span v-else class="cond muted">否则</span>
            <span class="arrow">→</span>
            <span class="to">{{ b.toLabel }}</span>
          </div>
        </div>
      </v-card>
    </template>
    <div class="conn">↓</div>
    <div class="endpoint">■ 结束</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  graph: { type: Object, required: true }, // {nodes:[{id,use}], edges:[{from,to,when?}]}
  primitives: { type: Object, default: () => ({}) }, // name -> {kind, budget, ...}（来自 /primitives）
})

// 原语人话名（卡片标题）。
const LABELS = {
  clarify: '澄清需求',
  frame: '分配维度',
  fanout: '并行出候选',
  turn: '轮流发言',
  schedule: '主持人调度',
  plan: '主持人现编',
  human_gate: '真人插话窗口',
  curate_gate: '人工策展',
  synthesize: '主笔综合',
}
// next_decision 取值人话化。
const DECISIONS = {
  next_speaker: '有人发言',
  yield_to_human: '让位真人',
  stop: '停止',
  continue: '继续讨论',
  end: '结束收尾',
  curate: '继续策展',
  fanout: '并行候选',
  speak: '指定发言',
  synthesize: '收尾',
}
const KIND_ZH = { transform: '变换', router: '路由', human: '人在环' }
const KIND_COLOR = { transform: 'primary', router: 'success', human: 'warning' }

const nodes = computed(() => props.graph?.nodes || [])
const edges = computed(() => props.graph?.edges || [])
const nodeById = computed(() => Object.fromEntries(nodes.value.map((n) => [n.id, n])))

// DFS 前序排出竖向流（从 START；未达节点按原序补尾）。
const ordered = computed(() => {
  const succ = {}
  for (const e of edges.value) (succ[e.from] ||= []).push(e.to)
  const seen = new Set()
  const out = []
  const visit = (id) => {
    if (id === 'START' || id === 'END' || seen.has(id)) return
    seen.add(id)
    const n = nodeById.value[id]
    if (n) out.push(n)
    for (const t of succ[id] || []) visit(t)
  }
  for (const t of succ['START'] || []) visit(t)
  for (const n of nodes.value) if (!seen.has(n.id)) out.push(n) // 兜底补未达
  return out
})
const orderIndex = computed(() => Object.fromEntries(ordered.value.map((n, i) => [n.id, i])))

const label = (n) => LABELS[n.use] || n.use
const kind = (n) => props.primitives[n.use]?.kind || ''
const kindZh = (n) => KIND_ZH[kind(n)] || '?'
const kindColor = (n) => KIND_COLOR[kind(n)] || undefined
const kindClass = (n) => `k-${kind(n)}`
const budgetOf = (n) => {
  const b = props.primitives[n.use]?.budget
  return b ? `${b.count} ≥ ${b.limit}` : null
}

function humanizeWhen(when) {
  if (!when) return null
  if (when.all) return when.all.map(humanizeWhen).join(' 且 ')
  if (when.any) return when.any.map(humanizeWhen).join(' 或 ')
  const { field, op, value } = when
  if (field === 'next_decision' && op === '==') return DECISIONS[value] || value
  return `${field} ${op} ${value}`
}

function branchesOf(id) {
  const here = orderIndex.value[id]
  return edges.value
    .filter((e) => e.from === id)
    .map((e) => {
      const back = e.to !== 'END' && (e.to === id || orderIndex.value[e.to] <= here)
      const toLabel = e.to === 'END' ? '结束' : label(nodeById.value[e.to] || { use: e.to })
      return { cond: humanizeWhen(e.when), to: e.to, toLabel, back }
    })
}
</script>

<style scoped>
.flow {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.endpoint {
  font-size: 0.85rem;
  opacity: 0.7;
  padding: 2px 10px;
}
.conn {
  opacity: 0.4;
  line-height: 1;
}
.node {
  width: 100%;
  max-width: 460px;
  padding: 10px 14px;
}
.node.k-router {
  border-left: 3px solid rgb(56, 142, 60);
}
.node.k-human {
  border-left: 3px solid rgb(245, 124, 0);
}
.node.k-transform {
  border-left: 3px solid rgb(25, 118, 210);
}
.node-head {
  display: flex;
  align-items: center;
}
.node-head .title {
  font-weight: 600;
}
.node-id {
  font-size: 0.72rem;
  margin-top: 1px;
}
.branches {
  margin-top: 6px;
  border-top: 1px dashed rgba(127, 127, 127, 0.3);
  padding-top: 5px;
}
.branch {
  font-size: 0.8rem;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 1px 0;
}
.branch.back {
  opacity: 0.8;
}
.branch .loop {
  color: rgb(245, 124, 0);
  font-weight: 600;
}
.branch .cond.muted {
  opacity: 0.6;
}
.branch .arrow {
  opacity: 0.5;
}
.branch .to {
  font-weight: 500;
}
</style>
