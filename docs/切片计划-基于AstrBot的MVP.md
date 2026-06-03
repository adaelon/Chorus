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

**S3.1b 点账本基座 + context 投影器（§6.11）**
- 做：`Claim{speaker_id,text,turn}` 模型 + `GroupState.claims`；把 `generate.py` 写死的 `history[-10:]` 抽成可插拔 `build_context(history, claims)->messages`，默认实现 = 远场全部 claims（带归属）+ 近场最近 K 轮原文（K 可配）。`claims` 为空时退化为纯原文窗口（行为兼容现状）。
- 不做：提点（S3.1c）；点的状态/对立关系。
- 判据：`pytest` — 投影器单测：给 history+claims，远场只出点、近场出原文；claims 空时等价原文窗口。原 fanout/turn 测试仍绿。

**S3.1c 提点原语 + TURN 集成（§6.11）**
- 做：中立 claim extractor（`structured_invoke` text_json 提 1-3 个点，§6.9）；TURN 发言后对该文本提点、追加进 `state.claims`。
- 不做：发言时一并产出（破坏 SSE）；提取质量调优。
- 判据：`pytest` — 两轮 TURN 后 `claims` 含两人归属；第三轮发言 prompt 里**远场只见点、不见第一轮原文全文**（注入假 extractor，离线）。

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

**S3.6 群视图 + 圆桌 live wire（含 S3.1c/S3.4/S3.5 遗留收口）✅**（拆 a-e 五刀：a CLARIFY live wire / b 圆桌 SYNTHESIZE 变体 / c 圆桌图上挂+SSE 起场 / d 续场+插话 / e ChatPage 群视图；详见代码链路）
- 做（后端 wire，先于前端）：
  1. **圆桌 service 端点**：`POST /roundtable`（起一场，初始 request 进 history、pending_human=None）+ `POST /roundtable/{key}/resume`（续：`{interject: text|null}` 转 `Command(resume=...)`，注意**非空** payload 约束）+ 插话异步注入（`aupdate_state` 写 pending_human）。`build_roundtable_recipe(..., human_in_loop=True)`，wire `human_gate`。
  2. **CLARIFY live wire（S3.5 遗留）**：把 `default_clarifier` 接进 service；`/inbound`、`/roundtable` 的 interrupt 处理改为**按 `payload["type"]` 分流**（`clarify` vs `curate` vs `human_gate`），前端据 type 渲染（澄清问 / 候选 / 插话窗口）。SSE 同样按 type 出事件。
  3. **圆桌 SYNTHESIZE 主笔综合（S3.3 遗留）**：圆桌无 candidates/picked → 现产空串；补一个从 `claims`/`history` 综合的产出（可在 synthesize 加分支或新综合节点，注意这会动节点——评估是否值得，或做成圆桌专用 synthesize 变体）。
- 做（前端）：复用 `ChatPage`，多 AI 身份气泡（按 sender 区分头像/名）+ 人插话输入框 + 澄清问气泡（可答/跳过），连 `brainApi`。
- 不做：配方选择（S3.7）。
- 判据：`pytest`（圆桌端点起/续/插话/澄清分流）；浏览器 — 圆桌多身份气泡正确归属、插话被接住、模糊需求弹澄清问（手动步骤）。
- **遗留取舍记录**：① `clarify` 的 `assess`(信心 LLM) 在 interrupt resume 时会重跑一次（assess 之后才 interrupt，节点整体重执行）——MVP 接受；接真实 LLM 后若成本敏感，拆成 `clarify(只assess)→Command(goto)→await_clarify(只interrupt)` 两节点（仿 curate 分离 LLM 与 interrupt）。② checkpoint 反序列化有 msgpack unregistered-type 警告（`AgentSlot/Msg/Claim`），未来 LangGraph 版本会 block——需注册类型或调 serde。

**S3.7 RecipePicker（L1 选配方）✅**
- 做：入口让用户在「圆桌」「扇出」间选配方启动一场。
- 不做：L2 荐配方 / L3 自拼（§6.6 留后）。
- 判据：浏览器 — 选圆桌走群视图、选扇出走 CuratePage（手动步骤）。
- 落地：Home 页改成两卡配方选择（圆桌→/roundtable、扇出→/curate）+ 连通 chip；vite build 通过，浏览器手动验。详见代码链路。

---

## S4 AstrBot 桥 + Telegram 多 bot

**S4.1 group_relay 插件骨架 + 出站 ✅**
- 做：AstrBot 侧 `Star` 插件；暴露 `/outbound`，按 `bot_id` 选 platform 实例 → `send_by_session`（先单 bot 验）。
- 不做：入站、去重、多 bot。
- 判据：`curl /outbound` → 指定 bot 在目标会话发出消息（单 bot 手动确认）。
- 落地：`astrbot/data/plugins/group_relay/`（vendored astrbot，仅插件进 git）；自起 aiohttp 桥 127.0.0.1:9876；出站逻辑离线 6 测 + 真实 astrbot 导入校验通过；curl 单 bot 手动验。详见代码链路。

**S4.2 入站转发 + 去重 + stop_event ✅**
- 做：群消息钩子 → 规范化 `InboundMsg` → POST 大脑 `/inbound`；按 `(group_key, native_msg_id)` 去重；`stop_event()` 防自动回复。
- 不做：多 bot（S4.3）。
- 判据：`pytest`(插件逻辑) — 同一 msg_id 两次只转发一次；发消息后 AstrBot 自身 LLM **不**回复（手动确认）。
- 落地：`inbound.py`（Dedup/decide/make_inbound_msg 纯逻辑）+ `main.py:on_group_message` 钩子；离线 12 测（含去重）+ 真实 astrbot 导入校验；无自动回复手动验。大脑侧 InboundMsg 适配留 S4.4。详见代码链路。

**S4.3 Telegram 多 bot 配置 + 映射 ✅**
- 做：AstrBot config 配 N 个 telegram platform 实例（各 token）；`Contact.bot_ref` ↔ 实例 id 映射；关 privacy mode。
- 不做：端到端联调（S4.4）。
- 判据：N 个 bot 都 RUNNING；大脑发 `/outbound` 能精确路由到对应 bot（手动确认）。
- 落地：`ContactIn.bot_ref` + `bot_ref_provider_from` + `OutboundClient`(contact→bot_ref→POST 桥) + ContactsPage bot_ref 字段；orchestrator 87 测 + 出站精确路由 smoke PASS（真 astrbot 两假 platform）。真 token/关 privacy 手动（README）。详见代码链路。

**S4.4 端到端联调 ✅**（拆 a/b：a 入站起圆桌+后台多轮+出站推群 / b 人插话改向）
- 做：把大脑产出经桥推进 Telegram 群；人在群里发问/插话经桥回大脑。
- 不做：QQ 官方（需求 §10，Phase 后）。
- 判据：Telegram 群发问 → N bot **各以独立身份**冒泡发言 → 人插话被接住改向（端到端手动验证脚本/录屏）。
- 落地：`app/relay.py:RelayDriver`（canonical_thread + 后台 step-loop 多轮 + 插话队列消费）+ `/relay/inbound` + `roster_provider`（有 bot_ref 的 Contact）+ 插件入站投 /relay/inbound。orchestrator 93 测（含端到端改向）。真 telegram 群发问→N bot 轮流→插话改向 = 手动验。详见代码链路。

**S4.5 PlatformPage 复用成 N bot 管理 —— 缓做（可选）**
- 做：复用 dashboard `PlatformPage` 管 N 个 bot 实例，连 `adminApi`(AstrBot)。
- 不做：产品域逻辑。
- 判据：浏览器 — 增删/启停 bot 实例，状态正确（手动步骤）。
- **缓做原因（2026-06-02）**：AstrBot 自带 dashboard 已能管平台实例（实测配 ada1/ada2 即用此）；搬进 Chorus web 会把产品 UI 重新耦合回 adminApi(AstrBot)，与 §6.12（transport 无关）相悖；属管理域、当前低产品价值（§6.8 用到再移）。**何时回头**：要做统一运维台（Chorus web 管一切、对运维藏掉 AstrBot）时。

---

## S5 配方核心化（transport/runtime 分层 + 主持人组原语，§6.12/§6.13）

> S3.6/S4.4 让 web 与 telegram 各写了一套驱动同一张圆桌图——重复且每加一种适配再抄一份。
> S5 把驱动收进 transport 无关的运行时，再让主持人按任务组合原语。先地基(S5.0)，后能力(S5.1/5.2)。

**S5.0 transport 无关会话运行时（统一 web/telegram 驱动）✅**（§6.12，A3 重构）
- 做：抽 `SessionRuntime` + 中性事件（入站 `Start|HumanMsg`；出站 `Turn|Ask|Result|Status|Done`）；把圆桌驱动逻辑收进 runtime（**一份**）；web `/roundtable` SSE 与 telegram `RelayDriver` 改为 runtime 的两个 **adapter**（各做"中性事件 ↔ transport"互转）；`group_key`/`bot_ref` 在 adapter 边界规范化成 `session_id`/`identity_id`。
- 不做：L2 选配方 / L3 组原语（S5.1/5.2）；改原语或圆桌图拓扑（纯搬运，行为不变）。
- 判据：`pytest` — runtime 出 OutboundEvent 序列正确（起场→`Turn*`→`Done`；插话→改向）；web/telegram 两 adapter 把**同一** runtime 事件各自映射对（离线假 transport）；**既有 web SSE 与 telegram 行为不变**（A3，原 `test_roundtable_service`/`test_relay` 仍绿或等价迁移）。

**S5.1 L2 主持人荐配方 ✅**（§6.13）
- 做：`select_recipe(task)->recipe` 廉价 LLM 调用，在**已测静态配方库**（圆桌/扇出）里按任务选；选不准兜底默认。
- 不做：L3 动态组原语。
- 判据：`pytest` — 给"讨论型/创作型"任务各选对配方；非法/低信心→默认兜底（注入假 selector 离线）。

**S5.2 L3 主持人组原语（auto 配方，带闸）✅**（§6.13，引擎能力；service 接入留后）
- 做：`decide_next` 泛化到 `Fanout|Speak|Curate|AskHuman|Synthesize|Stop`；引擎单循环 `PLAN→dispatch(原语)→PLAN`；做成库里一个 "auto" 配方（L1/L2 仍可选回静态配方兜底）；硬预算/步数闸。
- 不做：可视化自拼 DSL（更后）。
- 判据：`pytest` — auto 配方对一个任务组出"先 Fanout 后 Speak 轮转再 Synthesize"的合法序列（注入假 planner）；步数闸到顶必停；每步原语结果确定可验（§B2）。

**S5.1 修订（L2 荐配方收尾 bug，前端）✅**
- 病：首页"让主持人选"选完**同步即跳转**——`pickMsg`（含理由）随页面卸载没机会显示；且没把任务带到目标页，目标页用硬编码默认议题，**用户需求丢失**。
- 做：`recommend()` 不再选完即跳，先把 `{recipe,reason}` 渲染成结果卡（展示主持人建议+理由）+「进入」按钮；`enter()` 经 `query:{task}` 带任务跳转；`ChatPage`/`CuratePage` 挂载时 `route.query.task` 优先回填 `topic`/`request`。
- 判据：`npm run build` 过；手动——选完看得到理由、进目标页议题已回填。

**S5.3 L3 通电（auto 运行时接线）🅿️ 待做（被 S5.4 取代/重定向）**（§6.13）
- 原计划：把 S5.2 的 `build_auto_recipe`（运行时 PLAN⇄dispatch 即兴）接到 `/auto/stream` + web 第三张卡。
- **为何待做**：auto 即兴流的产物**不可见、不可改、且和用户造的图是两套东西**。按 §6.16，L3 的真正形态是 **AI 产出一张 recipe DAG**，复用 L4 的编译器跑、画布渲染成卡片流——故 **L4（S5.4）是 L3 的地基，先 L4 后 L3**。本刀重定向为 **S5.5**（依赖 S5.4 全绿）。S5.2 引擎代码保留作运行时兜底。

---

## S5.4 配方升 L4：用户自造 DAG（§6.16，内核先行/UI 最后）

> 原语收敛成带 spec 的三态乐高 → 图原生 JSON 内核 → 配方库 → 卡片流画布。每点都拆成独立可验的一刀。

### S5.4.0 引擎地基（原语收敛，A3 行为不变）

**S5.4.0a 原语规格表 `PrimitiveSpec` + registry ⏳**
- 做：新增 `app/recipes/spec.py`——`PrimitiveSpec{name,kind(transform|router|human),reads,writes,needs,emits,args,budget}` + `REGISTRY: dict[name,(spec,node_builder)]`，登记现有 ~9 个用户可见原语（`extract/generate` 不入）。**纯新增，不改任何节点行为**。
- 判据：`pytest test_spec` — registry 自洽（每个 `needs⊆reads`、`router/human` 才有 `emits`、name 唯一）；既有测试全绿。

**S5.4.0b 路由出节点：human_gate ⏳**
- 做：`human_gate` 去掉 `Command(goto=...)`，改为只写 state delta + `next_decision∈{continue,end}`（interrupt 暂停留在节点）；`build_roundtable_recipe` 在 `human_gate` 后补一条条件边（continue→schedule / end→synthesize）。**A3 等价替换**。
- 判据：`test_roundtable`/`test_relay` 全绿（行为不变）。

**S5.4.0c 路由出节点：curate_gate ⏳**
- 做：`curate_interrupt_node`（更名 `curate_gate`）同样去 `Command(goto)`，改 delta + `next_decision∈{curate,synthesize}`；`build_fanout_recipe` 补条件边。**A3**。
- 判据：`test_fanout`/`test_curate` 全绿。

**S5.4.0d 预算闸声明式 ⏳**
- 做：`plan`/`schedule` 的步数/预算闸从节点内硬编码改由 `spec.budget=(计数字段,上限字段)` 驱动；编译器/运行时据 spec 自动在 router 前插闸。**A3**。
- 判据：`test_auto`（步数闸到顶必停）、`test_roundtable`（预算闸）全绿。

**S5.4.0e 两 synthesize 合一 ⏳**
- 做：`synthesize`/`synthesize_roundtable` 合成一个——按 `claims` 有无走圆桌主笔 / 扇出汇候选兜底。**A3**。
- 判据：圆桌、扇出两路产出测试全绿。

### S5.4.1 编译器（声明式 DAG → StateGraph）

**S5.4.1a `when` 小解释器 ⏳**
- 做：`app/recipes/cond.py`——`eval_cond(cond, state)->bool`，`cond={field,op,value}|{all:[...]}|{any:[...]}`，算子白名单 `== != > >= < <= in empty truthy`，字段限 `GroupState`。**数据非代码、无 eval**。
- 判据：`test_cond` — 各算子 + all/any + 非法字段/算子报错，穷举。

**S5.4.1b `compile_recipe(json)->StateGraph` 直译 ⏳**
- 做：`app/recipes/compile.py`——`nodes` 按 `use` 从 registry 取 node_builder→`add_node(id)`；无 `when` 边→`add_edge`；带 `when` 边按 `from` 归组→`add_conditional_edges`（读 state 跑 `eval_cond`，命中 `to`，无命中走 else 边）；START/END 直连。
- 判据：`test_compile` — 一张最小手写 JSON 编译后 `ainvoke` 跑通（注入假节点离线）。

**S5.4.1c 编译期校验 ⏳**
- 做：`validate_recipe(json)`——①每节点 `needs` 在所有到达路径上被上游 `writes`（或初始输入）覆盖；②有条件出边的节点必有一条 else；③每个环上至少一个带 `budget` 的 router；④`when.field` 合法。报人话错误（供 S5.4.3 画布复用）。
- 判据：`test_validate` — 坏图（断前置/缺 else/无闸环/坏字段）各报对应错；好图通过。

**S5.4.1d 三配方改写成 JSON 等价替换 ⏳**
- 做：圆桌/扇出/auto 三个 `build_*_recipe` 改为加载内置 JSON 配方经 `compile_recipe` 产出；删手写拓扑。**A3 端到端等价**。
- 判据：既有全部端到端测试（roundtable/fanout/auto/relay）全绿。

### S5.4.2 配方库（存储 + 运行任意库内 DAG）

**S5.4.2a recipe 表 + CRUD ⏳**
- 做：注册表 db 加 `recipe(id,name,json,builtin)` + `/recipes` CRUD（仿 contacts）；三内置配方 seed 进库。
- 判据：`test_recipe_crud` — 增删改查 + 内置不可删。

**S5.4.2b `/recipe/run` 跑库内 DAG ⏳**
- 做：`POST /recipe/run {recipe_id, group_key, request, roster}`——取 JSON→`validate`→`compile`→跑（复用 `_sse_from_events`/`iter_events` 流式）。
- 判据：`test_recipe_run` — 按 id 取内置圆桌配方端到端流式跑通（离线假节点）。

### S5.4.3 卡片流画布（L4 对运营的门面）

**S5.4.3a 原语→卡片库投影 API ⏳**
- 做：`GET /primitives` 返回 registry 的 spec 列表（name/kind/args/可接性）给前端建卡片面板。
- 判据：端点返回与 registry 一致（test）。

**S5.4.3b 只读渲染：DAG JSON → 竖向卡片流 ⏳**（呼应"AI 搭的图也要看得懂"）
- 做：前端把一张 recipe JSON 渲染成竖向卡片流（router/human 的分叉→卡上标注；环→"循环"卡），**先只读**。
- 判据：三内置配方各渲染成可读卡片流（手动）；`npm run build` 过。

**S5.4.3c 编辑：改参/增删卡 + 实时校验 ⏳**
- 做：卡片参数可调（滑块/开关 ↔ `args`/`when.value`）、增删卡、连接；前端调 `validate` 实时标红（复用 S5.4.1c）。
- 判据：改一张图存库再跑通（手动）；非法编辑实时拦截（手动）。

**S5.4.3d 三模板可改 + 存为新配方 ⏳**
- 做：画布从三内置模板起步，"另存为"写库（S5.4.2 CRUD）；首页 RecipePicker 列出库内自定义配方。
- 判据：从圆桌模板改出一张新配方、存库、首页可选、跑通（手动端到端）。

## S5.5 L3 真正通电：AI 产出 DAG（依赖 S5.4 全绿）🅿️ 待做

> 取代原 S5.3。L3 = planner 不再运行时即兴，而是**产出一张 recipe DAG**，复用 S5.4 的编译器跑、画布渲染成用户看得懂的卡片流。
- 做：`plan_recipe(task,roster)->recipe_json`（AI 按任务组出一张合法 DAG，经 `validate` 兜底/重生）；`/recipe/auto {task}` 产图→可存库/可在画布展示/可跑；首页"让 AI 搭一个"入口。
- 不做：AI 在画布上增量改图（更后）。
- 判据：`pytest` — 给一个任务，`plan_recipe` 出的 JSON 过 `validate` 且端到端跑通（注入假 planner 离线）；产出的图能被 S5.4.3b 渲染成卡片流。

---

## S6 发布（pip 安装即用，§6.15）

> 目标：`pipx install chorus` → `chorus serve` → 浏览器开 localhost 就是完整产品（圆桌/扇出/好友/L2 选配方），不碰 telegram 也能用。telegram 桥外置（§6.15：进不了 pip，分层必然）。

**S6.0 配置解耦 + chorus 包骨架 + CLI**
- 做：LLM 配置从 `talk-agent/.env`（`CHORUS_DOTENV` 硬编码）换成**标准环境变量**（`CHORUS_LLM_BASE_URL/API_KEY/MODEL`，缺失给清晰报错）；`orchestrator/app/` → 顶层包 `chorus/`；`pyproject.toml` 填 `[project]`(name/version/deps) + `[project.scripts] chorus="chorus.cli:main"`；CLI `chorus serve --port`；sqlite 落 `~/.chorus/`（或 `--data-dir`）。
- 不做：打前端、发 PyPI（S6.1/6.2）。
- 判据：`pytest` 仍绿（配置/改包不破坏行为，A3）；`chorus serve` 起服务 `/health` 200；缺 LLM 配置时给清晰报错（手动/测）。

**S6.1 打进前端 dist + StaticFiles**
- 做：`npm run build` 的 `dist/` 作 package data 随包带；FastAPI `StaticFiles` 挂 `/`；一个进程同出 API+UI。
- 不做：PyPI 发布。
- 判据：本地装包后 `chorus serve` → 浏览器 localhost 出完整产品 UI（手动）。

**S6.2 PyPI 发布 + group_relay 独立分发**
- 做：`hatchling` build → `pipx install chorus` 干净环境验；`group_relay` 作**独立 AstrBot 插件**分发（自包含目录 + README，进不了 pip）；可选 docker-compose 全套（含 AstrBot/telegram）。
- 判据：干净环境 `pipx install chorus` → `chorus serve` 即用（手动/录屏）。

---

## 依赖与执行顺序

```
S1.1 → S1.2 → {S1.3, S1.4} → S1.5 → S1.6 ──┬─→ S1.7 → S1.8        (扇出端到端可用)
                                            │
S1.6 ─→ S2.0(durable checkpointer) ; S2.1 → S2.2 → S2.3   │   S2.4 需 S2.1 + 后端
S1.6 ─→ S3.0(interrupt 化扇出, 模型A) ──→ S3.4 复用同一 interrupt 机制
S2.* ─→ S3.1 → S3.1b(点账本+投影器) → S3.1c(中立提点) → S3.2 → S3.3(验收) → S3.4 → S3.5 ; S3.6/S3.7 需前端基座(S1.7)
S3(引擎) ─→ S4.1 → S4.2 → S4.3 → S4.4 ; S4.5 需 S1.7
S3.6 + S4.4 ─→ S5.0(runtime/transport 分层, 统一驱动) → S5.1(L2 荐配方) → S5.2(L3 组原语, 引擎)
S5.2 ─→ S5.4.0(引擎地基) → S5.4.1(编译器) → S5.4.2(配方库) → S5.4.3(卡片流画布) → S5.5(L3 产出 DAG)
   注：S5.3(L3 运行时接线) 待做，被 S5.4→S5.5 取代（先 L4 后 L3，§6.16）
S5(core 稳) ─→ S6.0(配置解耦+包骨架+CLI) → S6.1(打进前端 dist) → S6.2(PyPI 发布 + group_relay 独立分发)
```

**三个关键验收点**：
1. **S1.6 / S1.8**：扇出策展端到端跑通 = 编辑场景 MVP 价值落地。
2. **S3.3**：用已有原语零改引擎拼出第二张配方 = §6.6 配方抽象成立。
3. **S4.4**：Telegram 真实多 bot 多身份 = 平台适配验证（迁 QQ 前的护栏）。

**落盘原则**：每刀完成当场 commit + 跑判据 + 回本文勾掉对应行；下一会话从文件状态接手，不依赖上下文记忆。
