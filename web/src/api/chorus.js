import { brainApi } from './brain'

// 扇出策展三调用（对应 orchestrator service S1.6）。
export const inbound = (group_key, request, roster) =>
  brainApi.post('/inbound', { group_key, request, roster }).then((r) => r.data)

export const curate = (group_key, commands) =>
  brainApi.post('/curate', { group_key, commands }).then((r) => r.data)

export const synthesize = (group_key) =>
  brainApi.post('/synthesize', { group_key }).then((r) => r.data)
