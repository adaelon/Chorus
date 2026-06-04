// L4 配方卡片的人话标签（RecipeFlow 只读 / RecipeEditor 编辑共用，§6.16）。

// 原语 use → 人话名（卡片标题）。
export const PRIMITIVE_LABELS = {
  clarify: '澄清需求',
  frame: '分配维度',
  fanout: '并行出候选',
  turn: '轮流发言',
  schedule: '主持人调度',
  plan: '主持人现编',
  human_gate: '真人插话窗口',
  curate_gate: '人工策展',
  synthesize: '出结论/综合',
  produce: '出产物',
  deliver: '选产出形态',
}

// next_decision 取值 → 人话（边条件标注）。
export const DECISION_LABELS = {
  next_speaker: '有人发言',
  yield_to_human: '让位真人',
  stop: '停止',
  continue: '继续讨论',
  end: '结束收尾',
  curate: '继续策展',
  fanout: '并行候选',
  speak: '指定发言',
  synthesize: '收尾',
  produce: '出产物',
  decide: '出结论',
}

export const KIND_ZH = { transform: '变换', router: '路由', human: '人在环' }
export const KIND_COLOR = { transform: 'primary', router: 'success', human: 'warning' }

export const labelOf = (use) => PRIMITIVE_LABELS[use] || use
export const decisionLabel = (v) => DECISION_LABELS[v] || v

// 边条件 when → 人话；null/undefined → null（无条件/else）。
export function humanizeWhen(when) {
  if (!when) return null
  if (when.all) return when.all.map(humanizeWhen).join(' 且 ')
  if (when.any) return when.any.map(humanizeWhen).join(' 或 ')
  const { field, op, value } = when
  if (field === 'next_decision' && op === '==') return decisionLabel(value)
  return `${field} ${op} ${value}`
}
