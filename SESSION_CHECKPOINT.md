# SESSION_CHECKPOINT · 热启动盘

> 下一对话的**入口 + 热内存**。**单幅覆盖写、零累积**——每会话整页重写，不追加历史。
> 本盘是【路由器 + 在途状态】，**不是架构本体**：架构看 `docs/`，本盘只回答「现在在哪 · 下一步做什么 · 哪些还没刷盘」。过期即弃。
> 快照：2026-06-07 · 写于 HEAD `7394348`（trace 命令行风格指令）

---

## ⏱️ 第一件事：新鲜度自检（30 秒）

跑 `git log --oneline -1`：
- **= `7394348`** → 本盘新鲜，照读下文。
- **≠ `7394348`** → HEAD 已前移 → 以 git 为准：看 `git status -s` + `docs/代码链路.md` 尾部最近条目，重新理解现状后**重写本盘**。

---

## 🧭 冷启动读序（第一次接手项目，按序读）

1. **本盘**（你在这）— 拿到「当下在哪」。
2. `docs/技术方案-基于AstrBot的MVP实现.md` → **§0 结论先行 + §1 架构骨架 + §10 配方抽象**（约 30 行懂全局：AstrBot 瘦适配 + LangGraph 大脑 + 配方=可组合 DAG）。
3. `docs/代码链路.md` → **尾部最近 5–8 条**（看最近在动哪些文件、为什么）。
4. `docs/切片计划-基于AstrBot的MVP.md` → 切片清单与 ✅/🅿️ 进度。
5. 要查某决策来龙去脉 → 技术方案 **§6.x**（按编号检索）。

---

## 🚦 信号灯（一眼看状态）

| 面 | 状态 | 说明 |
|---|---|---|
| **执行层 S11–S14** | 🟢 全线闭合 + 实测通过 | 沙箱/MCP/内置工具均已真实联调，Bug 全修 |
| MVP 主线 S1–S14 | 🟢 基本完成 | 圆桌/扇出/多 bot/配方 L1–L4/历史重试/执行层全通 |
| **无在途切片** | ⚪ 待定方向 | 下一步是开放议题，非排队中的刀（见下） |
| 子群/群递归 S5.6 | 🟡 设计中 | `docs/子群对话.md`（草稿未跟踪）；todo 第 2 条 |
| 流式体感 bug | 🔴 待复现 | todo 第 1 条，与记忆 [[chorus-model-and-latency]] 冲突 |

---

## 🔥 热内存（楔合点 = 从这接手）

**本会话做了什么**：

1. **补全执行层启动指南** `docs/启动指南.md §四`——OpenSandbox 安装/启动、MCP args 填法（每个预设单独示例，`command=npx` / `args=-y @modelcontextprotocol/server-filesystem C:\目录`），以及 `CHORUS_SANDBOX_DOMAIN=127.0.0.1:8080`（host:port，不带 `http://`）的沙箱域名格式。

2. **修复实测四个 Bug**：
   - `execution_opensandbox.py:_default_readiness` — probe URL 补 `http://` + `/health`（旧：裸 domain → `sandbox_unavailable`）
   - `execution_opensandbox.py:_DEFAULT_IMAGE` — 默认镜像改 `latest`；`_default_factory` + `OpenSandboxBackend` 新增 `image` 参；`server.py` 读 `CHORUS_SANDBOX_IMAGE` env
   - `execution_mcp.py` — `intent.args or {}` 非 `or None`（空 dict 是假值，MCP server 侧 `received undefined`）
   - `tool_dispatch.py` — 成功路 `used_attempts=0` 重置（旧：累积→第二工具 while 不进→`tool_failed`）

3. **trace 抽屉展示可读指令**：
   - `turn.py:_run_tool_phase` emit `tool_call` 新增 `args` 字段
   - `ChatPage.vue` — `formatInstruction(tool_name, args)` 按工具名出 shell 风格字符串（`cat "path"` / `write "path"\n---\ncontent` / `web_search "query"` / `list_allowed_directories()` / 通用兜底 `tool(k=v)`）；无参工具也有显示；一个 `<pre>` 块合并 sandbox 代码与 MCP 指令

**工作树**：干净，无未提交改动。仅剩三个未跟踪草稿（见下）。

---

## 🧭 在途议题（todo.md/txt 原话，未转切片）

1. **流式体感**：出两字后整体蹦、非逐字 — 需 live curl 带时间戳复现（S3.6g 用过此法证伪过一次）。
2. **群递归**：原语怎么支持群嵌套 → `子群对话.md` 草稿（= S5.6 breakout 原语）。
3. **gateway**：配方库怎么适配 telegram。
4. **流程图→AI 圆桌讨论→分头负责各自代码**：新协作形态设想（疑似新配方+breakout）。
5. **好友设计**：每人 llm 后端选择（S7.4 已落；此条疑旧念或想再演进 → 待澄清）。

---

## ⚠️ 工作区未跟踪草稿（你的东西，未替你决定是否进 git）

- `todo.md` / `todo.txt` — 待办笔记
- `docs/子群对话.md` — S5.6 群递归设计草稿

（sqlite-shm/wal 已被 `.gitignore` 永久挡住，不再冒头。）

---

## 🔄 何时刷新本盘（事件触发，非持续）

- 收口一刀（commit 时）· 方向/决策变更 · 上下文将断（长任务中途）· 会话结束前。
- **不**在每次小改后追加——那是累积，违背本盘。刷新 = 整页覆写 + 更新顶部「写于 HEAD」。
