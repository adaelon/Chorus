import axios from 'axios'

// brainApi: 指向 LangGraph 编排服务（S1.6 / app.server，默认本地 :8900）。
// 用 VITE_BRAIN_BASE_URL 覆盖。adminApi(AstrBot) 等用到时再加。
export const brainApi = axios.create({
  baseURL: import.meta.env.VITE_BRAIN_BASE_URL || 'http://127.0.0.1:8900',
  timeout: 120000,
})
