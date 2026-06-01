# Chorus Web（产品前端·精简骨架）

精简 Vue3 + Vuetify SPA。**不整包 fork** AstrBot dashboard——按 §6.8（修订版）
"按需移植"其核心外壳件（流式 markdown 气泡 / JWT 鉴权 / 主题）到真正用到的切片。

S1.7 只含：空白产品页 + 路由 + 主题 + `brainApi` axios + 连通验证。

## 运行 & 验证（S1.7 判据，需在本机执行）

1. 先起编排服务（一个终端，在 `orchestrator/` 目录）：
   ```cmd
   :: Windows cmd（注意反斜杠）
   .venv\Scripts\python -m app.server       :: 监听 http://127.0.0.1:8900
   ```
   ```powershell
   # PowerShell
   .venv\Scripts\python.exe -m app.server
   ```
   ```bash
   # bash / git-bash
   .venv/Scripts/python -m app.server
   ```
2. 起前端（另一个终端，在 `web/` 目录）：
   ```bash
   npm install
   npm run dev                              # http://localhost:5173
   ```
3. 浏览器打开 `http://localhost:5173` → 看到 “Chorus 产品骨架” → 点 **Ping brainApi**
   → 出现绿色 `200 OK` 即连通（Network 面板可见 `GET :8900/health` 200）。

`brainApi` 地址默认 `http://127.0.0.1:8900`，可用环境变量 `VITE_BRAIN_BASE_URL` 覆盖。
