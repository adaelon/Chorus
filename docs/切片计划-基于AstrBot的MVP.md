# 切片计划：基于 AstrBot 的「AI 微信群」MVP

> 把[技术方案](技术方案-基于AstrBot的MVP实现.md) §7 的四个大切片（S1-S4）按小切片工作法拆成"一次一刀、刀刀可验"。
> 每刀三段式：**做什么 / 不做什么 / 完成判据**。判据优先用确定性命令（测试/脚本），UI 无组件测试框架处写明浏览器手动步骤。
> 依赖与执行顺序见 §末。状态：计划，未写代码。

## 本文遵循的纪律

- **A1 切片声明**：每刀只动一件事；"顺便"= 切片污染，拎成下一刀。
- **A2 测试契约**：改已有路径→跑相关测试仍绿；新路径→加最小测试；修 bug→先写复现失败测试(red-green)；UI 无框架→写明手动验证步骤，不假装有覆盖。
- **执行规则**：一次一刀，**判据绿了才下一刀**；每刀完成代码 commit-ready + 本文勾掉，不靠会话记忆。
- 栈约定：引擎/服务 = Python + LangGraph，测试 `pytest`；前端 = fork 自 AstrBot dashboard(Vue3+Vuetify)，纯逻辑测试 `node --test`(`.test.mjs`)。

---

## S1 扇出配方端到端（编辑场景，MVP 第一块价值）

### 引擎 / 服务

**S1.1 服务骨架 + GroupState + checkpointer**
- 做：起 LangGraph 服务进程；定义 `GroupState`(pydantic)；接 `SqliteSaver`；一张空图能 `invoke` 并持久化 state。
- 不做：任何节点逻辑、LLM 调用、API 路由、前端。
- 判据：`pytest` — invoke 空图 → state 落 sqlite → 重启进程 reload 出**同一** state。

**S1.2 LLM 客户端封装 + retry**
- 做：封装 `ChatOpenAI(base_url=.../v1)`；用 tenacity 包 retry（实测偶发断连，技术方案 §8）。
- 不做：任何节点/业务逻辑。
- 判据：`pytest` — mock 首次断连，retry 后成功返回；对真实 `base_url` 跑一次 smoke 通过。

**S1.3 FANOUT 节点（并行生成）**
- 做：`FANOUT(n)` 节点——`asyncio.gather` 并行 N 个 agent，各自独立 prompt，产出 N 份候选写入 state。
- 不做：人设拼接（先用占位 prompt）、CURATE、持久化身份。
- 判据：`pytest` — 给 N 个 `AgentSlot` + 一个需求，返回 N 份候选；断言**并行**（总耗时 ≈ 单次，而非 N×）。

**S1.4 FRAME 节点（+ CLARIFY 占位）**
- 做：`FRAME` 主持人 LLM 读需求 → 给 roster 每人分 `dimension`；`CLARIFY` 先做成直通占位（恒"信心够"）。
- 不做：CLARIFY 真实信心自评（留 S3.5）。
- 判据：`pytest` — 给一个需求，roster 每个 slot 拿到非空 `dimension`。

**S1.4b 结构化输出策略抽象**（§6.9）
- 做：`app/structured.py:structured_invoke` 三策略（json_schema / function_calling / text_json）+ `LLM_STRUCTURED_METHOD` 配置（默认 text_json）；frame 改用它。
- 不做：对 json_schema / function_calling 做真实模型 smoke（当前后端不支持，接入新模型再验）。
- 判据：`pytest` — text_json 解析/容错单测（离线）+ frame 离线测试仍绿 + frame 真实 smoke 仍通过（经新抽象走 text_json）。
- 起因：S1.4 实测后端不支持 response_format/强制 tool_choice，避免把 text_json 写死过拟合；**S3.2 SCHEDULE 复用本抽象**。

**S1.5 CURATE 节点（人工策展指令）**
- 做：`CURATE` 接收并 apply `pick`/`eliminate`/`reassign` 指令到 state；`reassign(point_from_A, exec=B)` 触发对 B 的一次定向再生成。
- 不做：信誉写入（留 S2.3）、前端。
- 判据：`pytest` — 喂三类指令各一条，断言 state 正确变更；`reassign` 后 B 产出含该 point 的新文本。

**S1.6 扇出配方组装 + API**
- 做：把 S1.3-S1.5 节点按 `CLARIFY→FRAME→FANOUT→CURATE→〔reassign→TURN〕*→SYNTHESIZE` 接成图（recipe loader）；暴露 `/inbound`、`/curate` HTTP。
- 不做：圆桌配方、AstrBot 桥（用 curl/CLI 当 mock 适配器）。
- 判据：脚本端到端 — `curl /inbound`(需求) → 返回 N 候选 → `curl /curate`(指令) → 返回策展结果，序列正确。

### 前端（继承改造，技术方案 §12）

**S1.7 前端基座**（精简骨架，§6.8 修订版）
- 做：建精简 Vue3+Vuetify SPA `web/`（空白产品页 + 路由 + 主题）；配 `brainApi` axios（默认 :8900，`VITE_BRAIN_BASE_URL` 可覆盖）；后端加 `GET /health` + CORS 供连通验证。**不整包 fork**、不拖 monaco/charts。
- 不做：CuratePage 逻辑（S1.8）、管理域、外壳件移植（用到时再移）。
- 判据：后端 `pytest` /health 绿（可自动验）；前端 `npm install && npm run dev` 起得来、浏览器 Ping brainApi 见 200（**本机浏览器手动验**）。

**S1.8 CuratePage 最小版**
- 做：N 候选并排展示 + `pick`/`eliminate`/`reassign` 操作，连 `brainApi`(S1.6)。
- 不做：群视图、配方选择、好友库选人（占位 roster）。
- 判据：浏览器 — 提需求 → 看到 N 候选 → 点 pick/eliminate/reassign → 结果更新正确（手动步骤写入 PR 描述）。

---

## S2 持久层 + 混合身份 + 信誉

**S2.0 服务 durable checkpointer**（还 S1.6 欠的债）
- 做：服务 checkpointer 从 `MemorySaver` 换成 `AsyncSqliteSaver`（`langgraph.checkpoint.sqlite.aio`，加 `aiosqlite` 依赖）；用 FastAPI **lifespan** 进入其异步 context、在启动时建图挂 `app.state`、关闭时退出；`create_app` 改为 lifespan 内建图。
- 不做：业务逻辑改动；Contact/Group/Message 落库（S2.1）。
- 判据：`pytest` — 同一 db 文件"重启"（重建 app）后，之前 `/inbound` 的群 `candidates` 仍在（服务层版的 S1.1 判据）；`MemorySaver` 仍可注入给离线测试。
- 起因：S1.6 服务用内存 checkpointer，重启丢群状态；编辑跨时间多轮策展不能丢。

**S2.1 数据模型 + 迁移**
- 做：`Contact`/`Group`/`Message` SQLModel schema（技术方案 §5）+ 建表/迁移。
- 不做：信誉字段（留 S2.3）、人设注入、UI。
- 判据：`pytest` — 建表后增删查改各模型一条；重启后数据在。

**S2.2 混合身份注入**
- 做：`基础人设 + 临场维度 + 群历史` 拼 prompt（§4），接进 FANOUT/TURN，替换 S1.3 占位。
- 不做：跨群长期记忆（MVP 只本群短期，§5 注）。
- 判据：`pytest` — 同一 `Contact` 两场被分到**不同维度**，注入的 prompt 两段拼接正确。

**S2.3 信誉软加权字段**
- 做：Contact 加信誉字段；`eliminate` 写信誉（只影响本场/下次邀请，**可逆**，§10.5）。
- 不做：用信誉做"处决"或全局淘汰（§8 否决）。
- 判据：`pytest` — eliminate 后信誉降、且下场仍可被邀（可逆性断言）。

**S2.4 PersonaPage 复用成 Contact 注册表**
- 做：复用 dashboard `PersonaPage` UI，CRUD 连 `brainApi` 管 `Contact`。
- 不做：bot_ref 绑定真实 bot（留 S4）。
- 判据：浏览器 — 新建/编辑/删除 Contact，刷新后在；CuratePage 能从库选好友入场（手动步骤）。

---

## S3 圆桌配方 + 群视图（抽象验收点）

**S3.0 配方引擎 interrupt 化（人在环统一，模型 A，§6.10）**
- 做：扇出配方迁成**一张图** `clarify→frame→fanout→[interrupt: curate 循环]→synthesize`：`curate` 用 LangGraph `interrupt`（暂停—等人—`resume`，多轮循环）进图，`synthesize` 成图终端节点；service 层只剩"起/恢复图 + 转发 interrupt payload"。建立 S3.4 圆桌打断复用的同一 interrupt 机制。
- 不做：圆桌配方（S3.1+）；前端契约破坏（`/inbound`→候选、`/curate`→resume 尽量保持端点形状）。
- 判据：`pytest` — 原 e2e 行为不变仍绿（A3 重构）；新增 interrupt/resume 往返 + 多轮 curate 测试；`git diff` 显示业务步骤从 service 移入图、引擎无 if/else 特例。

**S3.1 TURN 节点**
- 做：`TURN` 单 agent 发言（能看到上文 `history`），产出追加 state，`turns_since_human += 1`。
- 不做：调度决策（S3.2）、并行。
- 判据：`pytest` — 连续两次 TURN，第二次 prompt 含第一次发言（上文可见性）。

**S3.2 SCHEDULE 节点**
- 做：`decide_next`(§3.2)——`pending_human` 优先 → 预算闸 → `moderator_llm_pick`，返回 `NextSpeaker|YieldToHuman|Stop`。`moderator_llm_pick` **复用 S1.4b 的 `structured_invoke`**（§6.9）。
- 不做：打断注入（S3.4）。
- 判据：`pytest` — 三个分支各构造一例输入，断言返回正确决策类型；到 `max_turns_per_human` 必返 Stop。

**S3.3 圆桌配方组装（零改引擎验收）**
- 做：用**已有原语**把圆桌配方 `CLARIFY→FRAME→(TURN⇄SCHEDULE)*→SYNTHESIZE` 接成图。
- 不做：新增/修改任何节点或引擎代码（这正是验收点）。
- 判据：圆桌配方端到端跑通 **且** `git diff` 显示只新增配方文件、**未动节点/引擎**（§6.6 抽象成立判据）。

**S3.4 人在环打断**
- 做：`pending_human` 注入通道 + `SCHEDULE` 每步先查 + `INTERRUPT` 横切。
- 不做：UI（S3.6）。
- 判据：`pytest` — 讨论中注入人类消息，下一步调度让位/改向（断言状态转移）。

**S3.5 CLARIFY 真实化**
- 做：信心自评 LLM 替换 S1.4 占位；不足→回述+一问→等用户，可跳过（§6.5，档位 B）。
- 不做：阈值自适应（Phase 2）。
- 判据：`pytest` — 模糊需求触发澄清问、清晰需求直通；"跳过"强制进 FRAME。

**S3.6 ChatPage 改造成群视图**
- 做：复用 `ChatPage`，加多 AI 身份气泡（按 sender 区分头像/名）+ 人插话输入框，连 `brainApi`。
- 不做：配方选择（S3.7）。
- 判据：浏览器 — 圆桌讨论中多身份气泡正确归属；输入插话被接住（手动步骤）。

**S3.7 RecipePicker（L1 选配方）**
- 做：入口让用户在「圆桌」「扇出」间选配方启动一场。
- 不做：L2 荐配方 / L3 自拼（§6.6 留后）。
- 判据：浏览器 — 选圆桌走群视图、选扇出走 CuratePage（手动步骤）。

---

## S4 AstrBot 桥 + Telegram 多 bot

**S4.1 group_relay 插件骨架 + 出站**
- 做：AstrBot 侧 `Star` 插件；暴露 `/outbound`，按 `bot_id` 选 platform 实例 → `send_by_session`（先单 bot 验）。
- 不做：入站、去重、多 bot。
- 判据：`curl /outbound` → 指定 bot 在目标会话发出消息（单 bot 手动确认）。

**S4.2 入站转发 + 去重 + stop_event**
- 做：群消息钩子 → 规范化 `InboundMsg` → POST 大脑 `/inbound`；按 `(group_key, native_msg_id)` 去重；`stop_event()` 防自动回复。
- 不做：多 bot（S4.3）。
- 判据：`pytest`(插件逻辑) — 同一 msg_id 两次只转发一次；发消息后 AstrBot 自身 LLM **不**回复（手动确认）。

**S4.3 Telegram 多 bot 配置 + 映射**
- 做：AstrBot config 配 N 个 telegram platform 实例（各 token）；`Contact.bot_ref` ↔ 实例 id 映射；关 privacy mode。
- 不做：端到端联调（S4.4）。
- 判据：N 个 bot 都 RUNNING；大脑发 `/outbound` 能精确路由到对应 bot（手动确认）。

**S4.4 端到端联调**
- 做：把大脑产出经桥推进 Telegram 群；人在群里发问/插话经桥回大脑。
- 不做：QQ 官方（需求 §10，Phase 后）。
- 判据：Telegram 群发问 → N bot **各以独立身份**冒泡发言 → 人插话被接住改向（端到端手动验证脚本/录屏）。

**S4.5 PlatformPage 复用成 N bot 管理**
- 做：复用 dashboard `PlatformPage` 管 N 个 bot 实例，连 `adminApi`(AstrBot)。
- 不做：产品域逻辑。
- 判据：浏览器 — 增删/启停 bot 实例，状态正确（手动步骤）。

---

## 依赖与执行顺序

```
S1.1 → S1.2 → {S1.3, S1.4} → S1.5 → S1.6 ──┬─→ S1.7 → S1.8        (扇出端到端可用)
                                            │
S1.6 ─→ S2.0(durable checkpointer) ; S2.1 → S2.2 → S2.3   │   S2.4 需 S2.1 + 后端
S1.6 ─→ S3.0(interrupt 化扇出, 模型A) ──→ S3.4 复用同一 interrupt 机制
S2.* ─→ S3.1 → S3.2 → S3.3(验收) → S3.4 → S3.5 ; S3.6/S3.7 需前端基座(S1.7)
S3(引擎) ─→ S4.1 → S4.2 → S4.3 → S4.4 ; S4.5 需 S1.7
```

**三个关键验收点**：
1. **S1.6 / S1.8**：扇出策展端到端跑通 = 编辑场景 MVP 价值落地。
2. **S3.3**：用已有原语零改引擎拼出第二张配方 = §6.6 配方抽象成立。
3. **S4.4**：Telegram 真实多 bot 多身份 = 平台适配验证（迁 QQ 前的护栏）。

**落盘原则**：每刀完成当场 commit + 跑判据 + 回本文勾掉对应行；下一会话从文件状态接手，不依赖上下文记忆。
