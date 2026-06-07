# SESSION_CHECKPOINT · 热启动盘

> 下一对话的**入口 + 热内存**。**单幅覆盖写、零累积**——每会话整页重写，不追加历史。
> 本盘是【路由器 + 在途状态】，**不是架构本体**：架构看 `docs/`，本盘只回答「现在在哪 · 下一步做什么 · 哪些还没刷盘」。过期即弃。
> 快照：2026-06-07 · 写于 HEAD `0b12117`（S13f.c）

---

## ⏱️ 第一件事：新鲜度自检（30 秒）

跑 `git log --oneline -1`：
- **= `0b12117`** → 本盘新鲜，照读下文。
- **≠ `0b12117`** → HEAD 已前移，下方「未刷盘增量」可能**已过期** → 以 git 为准：看 `git status -s` + `docs/代码链路.md` 尾部最近条目，重新理解现状后**重写本盘**。

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
| S13 执行层织入圆桌 | 🟢 已闭合 | 已提交 `0b12117`，沙箱+MCP 全链路通 |
| **S14a 内置工具** | 🟡 **完成未提交** | 代码+测试在工作区，doc 已标 ✅ 320 passed；**差 commit+push** |
| S14b MCP 预设一键加 | 🔴 未起（🅿️） | 纯前端，下一刀 |
| 子群/群递归（S5.6） | 🔴 设计中 | `docs/子群对话.md`（未跟踪）；todo 第 2 条 |
| 流式体感 bug | 🔴 待复现 | todo 第 1 条，与记忆 [[chorus-model-and-latency]] 冲突 |

---

## 🔥 热内存（楔合点 = 从这接手）

**上次会话动作**：读全三份架构文档理解项目 → 建本盘 + CLAUDE.md 引导链。S14a 代码仍躺工作区未提交。

**未刷盘增量 = S14a 一刀，已绿未提交**（明细见 `代码链路.md` S14a 条目）：
- 新增 `orchestrator/app/builtin_tools.py`（`fetch_url`/`web_search` ddgs 免 key + `BuiltinToolSession` mcp 兼容形状）
- 改 `execution_mcp.py`（`McpRegistry.include_builtins`）· `nodes/plan_stream.py`（`_plan_system` 加 `has_sandbox`）· `service.py`（传 `has_sandbox`）· `pyproject.toml`（`[tools] ddgs`）
- 新增 `tests/service/test_builtin_tools.py`(6) · 改 `test_mcp_servers.py`（`include_builtins=False` 隔离）

**下一步（二选一，问用户）**：
1. **收口 S14a** → commit+push main（[[auto-commit-each-slice]] 默认可直接做）；先剔脏件（见下）。
2. **起 S14b** → `McpServersPage`「从预设添加」官方 server（filesystem/fetch/git…）预填表单。

**验证**：`orchestrator/.venv` 跑 pytest（系统 Python 3.14 缺依赖，[[chorus-test-env]]）。

---

## 🧭 在途议题（todo.md/txt 原话，未转切片）

1. **流式体感**：出两字后整体蹦、非逐字 — 需 live curl 带时间戳复现（S3.6g 用过此法）。
2. **群递归**：原语怎么支持群嵌套 → `子群对话.md` 草稿（= S5.6 breakout）。
3. **gateway**：配方库怎么适配 telegram（telegram 侧目前只跑 roundtable）。
4. **skill 接入**：mcp/sandbox 已 S11–S14；skill（AstrBot SkillManager / Shipyard Neo）仍空间预留未落。
5. **流程图→AI 圆桌讨论→分头负责各自代码**：新协作形态设想（疑似新配方+breakout）。
6. **好友设计**：每人 llm 后端选择（S7.4 已落「AstrBot bot 作后端」；此条疑旧念或想再演进 → 待澄清）。

---

## ⚠️ 工作区脏件（提交前剔除）

- `orchestrator/group_checkpoints.sqlite-shm` / `.sqlite-wal` — 运行态，**勿提交**。`.gitignore` 只有 `*.sqlite`，未盖 shm/wal → **建议补 `*.sqlite-shm` `*.sqlite-wal`**。
- `todo.md` / `todo.txt` / `docs/子群对话.md` — 工作草稿，确认是否进 git 再 add，别误带。

---

## 🔄 何时刷新本盘（事件触发，非持续）

- 收口一刀（commit 时）· 方向/决策变更 · 上下文将断（长任务中途）· 会话结束前。
- **不**在每次小改后追加——那是累积，违背本盘。刷新 = 整页覆写 + 更新顶部「写于 HEAD」。
