import { brainApi } from './brain'

// 扇出策展三调用（对应 orchestrator service S1.6）。
export const inbound = (group_key, request, roster) =>
  brainApi.post('/inbound', { group_key, request, roster }).then((r) => r.data)

export const curate = (group_key, commands) =>
  brainApi.post('/curate', { group_key, commands }).then((r) => r.data)

export const synthesize = (group_key) =>
  brainApi.post('/synthesize', { group_key }).then((r) => r.data)

// 流式 /inbound（SSE over fetch；POST 故不用原生 EventSource）。
// handlers: { status, framed, delta, candidates, done, error } 按事件 type 分派。
export async function inboundStream(group_key, request, roster, handlers = {}) {
  const resp = await fetch(`${brainApi.defaults.baseURL}/inbound/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_key, request, roster }),
  })
  if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i)
      buf = buf.slice(i + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data:')) continue // ':' 开头是心跳注释，忽略
        const payload = line.slice(5).trim()
        if (!payload) continue
        let ev
        try {
          ev = JSON.parse(payload)
        } catch {
          continue
        }
        handlers[ev.type]?.(ev)
      }
    }
  }
}

// 通用 SSE over fetch（POST）：按事件 type 分派到 handlers。S3.6 圆桌端点复用。
async function streamPost(path, body, handlers = {}) {
  const resp = await fetch(`${brainApi.defaults.baseURL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i)
      buf = buf.slice(i + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data:')) continue // ':' 开头是心跳注释，忽略
        const payload = line.slice(5).trim()
        if (!payload) continue
        let ev
        try {
          ev = JSON.parse(payload)
        } catch {
          continue
        }
        handlers[ev.type]?.(ev)
      }
    }
  }
}

// 圆桌（S3.6c/d）：起场 / 续场(resume) / 异步插话。
// 起场 handlers: { framed, delta, turn, human_gate, clarify, output, done, error }。
export const roundtableStream = (group_key, request, roster, handlers) =>
  streamPost('/roundtable/stream', { group_key, request, roster }, handlers)

// resume: human_gate→{interject:text|null}；clarify→{answer:text}|{skip:true}。
export const roundtableResume = (group_key, resume, handlers) =>
  streamPost(`/roundtable/${group_key}/resume/stream`, resume, handlers)

export const roundtableInterject = (group_key, text) =>
  brainApi.post(`/roundtable/${group_key}/interject`, { text }).then((r) => r.data)

// L2 荐配方（S5.1）：按任务让主持人选 roundtable|fanout
export const selectRecipe = (task) =>
  brainApi.post('/recipe/select', { task }).then((r) => r.data)

// L4 配方库（S5.4.2/3）：原语卡片库 / 库内配方 CRUD / 跑库内 DAG
export const listPrimitives = () => brainApi.get('/primitives').then((r) => r.data)
export const listRecipes = () => brainApi.get('/recipes').then((r) => r.data)
export const getRecipe = (id) => brainApi.get(`/recipes/${id}`).then((r) => r.data)

// Contact 注册表 CRUD（S2.4）
export const listContacts = () => brainApi.get('/contacts').then((r) => r.data)
export const createContact = (c) => brainApi.post('/contacts', c).then((r) => r.data)
export const updateContact = (id, c) => brainApi.put(`/contacts/${id}`, c).then((r) => r.data)
export const deleteContact = (id) => brainApi.delete(`/contacts/${id}`).then((r) => r.data)
