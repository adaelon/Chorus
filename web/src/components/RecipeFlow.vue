<template>
  <div class="flow">
    <div class="endpoint">▷ 开始</div>
    <template v-for="(n, i) in ordered" :key="n.id">
      <!-- 与上一张卡有真实顺序边 → ↓；否则是上游分叉的“分支落点”，显式标注来源/条件，
           避免把互斥分支（如 deliver→{出产物|出结论}）误画成线性顺序 -->
      <div v-if="!conns[i]" class="conn">↓</div>
      <div v-else class="conn-branch">
        <span class="bl-head">↳ 由此到达：</span>{{ conns[i].text }}
      </div>
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
            <span v-else-if="b.isElse" class="cond muted">否则</span>
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
import { KIND_COLOR, KIND_ZH, humanizeWhen, labelOf } from '../utils/recipeLabels'

const props = defineProps({
  graph: { type: Object, required: true }, // {nodes:[{id,use}], edges:[{from,to,when?}]}
  primitives: { type: Object, default: () => ({}) }, // name -> {kind, budget, ...}（来自 /primitives）
})

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

// 每张卡的"上方连接符"：null=与上一张卡有真实顺序边（画 ↓）；否则是分支落点（来自上游分叉，
// 与上一张卡之间没有边）——把真实入边写成人话短句，避免互斥分支被竖排误读成线性流。
// 每条入边渲染成「『来源卡』的『条件』分支」；按上游就近排序（最近的分叉在前）。
const conns = computed(() =>
  ordered.value.map((n, i) => {
    const prevId = i === 0 ? 'START' : ordered.value[i - 1].id
    if (edges.value.some((e) => e.from === prevId && e.to === n.id)) return null
    const oi = orderIndex.value
    const srcs = edges.value
      .filter((e) => e.to === n.id)
      .map((e) => {
        const fromLabel = e.from === 'START' ? '开始' : label(nodeById.value[e.from] || { use: e.from })
        const cond = humanizeWhen(e.when)
        const isElse = !e.when && edges.value.some((x) => x.from === e.from && x.when)
        const tail = cond ? `的「${cond}」分支` : isElse ? '的「否则」分支' : ''
        return { text: `${fromLabel}${tail}`, order: e.from === 'START' ? -1 : (oi[e.from] ?? -1) }
      })
      .sort((a, b) => b.order - a.order)
    return { text: srcs.map((s) => s.text).join('，或 ') }
  }),
)

const label = (n) => labelOf(n.use)
const kind = (n) => props.primitives[n.use]?.kind || ''
const kindZh = (n) => KIND_ZH[kind(n)] || '?'
const kindColor = (n) => KIND_COLOR[kind(n)] || undefined
const kindClass = (n) => `k-${kind(n)}`
const budgetOf = (n) => {
  const b = props.primitives[n.use]?.budget
  return b ? `${b.count} ≥ ${b.limit}` : null
}

function branchesOf(id) {
  const here = orderIndex.value[id]
  const out = edges.value.filter((e) => e.from === id)
  const hasWhen = out.some((e) => e.when) // 有条件分支时，无 when 的那条才算"否则"
  return out.map((e) => {
    const back = e.to !== 'END' && (e.to === id || orderIndex.value[e.to] <= here)
    const toLabel = e.to === 'END' ? '结束' : label(nodeById.value[e.to] || { use: e.to })
    const cond = humanizeWhen(e.when)
    return { cond, isElse: !cond && hasWhen, to: e.to, toLabel, back }
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
.conn-branch {
  width: 100%;
  max-width: 460px;
  font-size: 0.74rem;
  color: rgb(245, 124, 0);
  background: rgba(245, 124, 0, 0.08);
  border: 1px dashed rgba(245, 124, 0, 0.5);
  border-radius: 6px;
  padding: 3px 10px;
  margin: 2px 0;
}
.conn-branch .bl-head {
  font-weight: 600;
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
