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

**S5.4.0a 原语规格表 `PrimitiveSpec` + registry ✅**
- 做：新增 `app/recipes_spec.py`（扁平，避与 `recipes.py` 冲突）——`PrimitiveSpec{name,kind,reads,writes,needs,emits,args,budget}` + `Primitive{spec,node}` + `REGISTRY`（登记 9 原语）+ `check_spec`/`validate_registry`。**纯新增，不改节点行为**。
- 判据：`tests/test_spec.py`（10 条）registry 自洽 + check_spec 拒坏 spec；`.venv` 全量 **115 passed, 2 skipped**（A3）。

**S5.4.0b 路由出节点：human_gate ✅**
- 做：`human_gate` 去掉 `Command(goto=...)`/`destinations`，返回纯 dict delta + `next_decision∈{continue,end}`（interrupt 暂停留在节点）；`build_roundtable_recipe` 加 `_route_after_gate` 条件边（end→synthesize / 否则 schedule）。**A3 等价替换**。
- 判据：`.venv` 全量 **115 passed, 2 skipped**；end/continue 两支由 test_human_gate 端到端覆盖。

**S5.4.0c 路由出节点：curate_gate ✅**
- 做：`curate_interrupt_node` 去 `Command(goto)`/`destinations`，返回 dict delta + `next_decision∈{curate,synthesize}`；`build_fanout_recipe` 加 `_route_after_curate` 条件边（含 **curate→curate 自循环回边**）。**A3**。（registry 名 `curate_gate`，节点函数名保留 `curate_interrupt_node`。）
- 判据：`.venv` 全量 **115 passed, 2 skipped**；自循环由 test_interrupt 多轮策展覆盖。

**S5.4.0d 预算闸声明式 ✅**
- 做：新增 `app/budget.py`——`Budget(count,limit,reason)` 描述符 + `budget_tripped`；`schedule`/`plan` 的闸从散落字面量改读注入的 `Budget`（默认各自 `SCHEDULE_BUDGET`/`PLAN_BUDGET`，直接调用照常受闸）；`spec.budget` 复用同一常量（单一来源，供编译器/画布读）。**A3**。
- 判据：`.venv` 全量 **117 passed, 2 skipped**（test_schedule/test_auto 步数闸 + 2 新 budget 测试）。

**S5.4.0e 两 synthesize 合一 ✅**
- 做：`synthesize`/`synthesize_roundtable` 合成一个 `synthesize(state,*,compose=None)`——分流：有 compose→主笔 / 有候选且无 claims→汇候选 / 否则兜底归并；三配方与 registry 改用统一节点。**A3**。
- 判据：`.venv` 全量 **117 passed, 2 skipped**（圆桌主笔/兜底/ai 史 + 扇出汇候选 + auto 全绿）。

> **S5.4.0 引擎地基（a–e）完成** ✅：原语已收敛成带 spec 的三态乐高——只写 state、路由全在边、闸声明式、synthesize 合一。下一步 S5.4.1 编译器起用这份 registry。

### S5.4.1 编译器（声明式 DAG → StateGraph）

**S5.4.1a `when` 小解释器 ✅**
- 做：`app/recipes_cond.py`（扁平）——`eval_cond(cond, state)->bool`，`cond={field,op,value}|{all:[...]}|{any:[...]}`（可嵌套），算子白名单 `== != > >= < <= in empty truthy`，字段限 `GroupState`。**数据非代码、无 eval**。
- 判据：`tests/test_cond.py`（11 条）各算子 + all/any 嵌套 + 非法字段/算子/非 dict/坏复合报错；`.venv` 全量 **128 passed, 2 skipped**。

**S5.4.1b `compile_recipe(json)->StateGraph` 直译 ✅**
- 做：`app/recipes_compile.py`（扁平）——`nodes` 按 `use` 取 registry 节点、`inspect` 过滤注入 deps + 据 `spec.budget` 插闸→`add_node`；单条无 when 出边→`add_edge`；否则按 `from` 归组→`add_conditional_edges`（`eval_cond` 顺序求值、无 when 边作 else）；`START/END` 字符串→常量。`args` 暂不处理（spec.args 全 None，留 1c/run）。
- 判据：`tests/test_compile.py`（4 条）最小 JSON 注入假节点 `ainvoke` 跑通 + 条件分流(next_decision) + 通用 when(turns_since_human) + 条件边直达 END + 未注册原语报错；`.venv` 全量 **132 passed, 2 skipped**。

**S5.4.1c 编译期校验 ✅**
- 做：`app/recipes_validate.py`——`validate_recipe(json)->list[str]`（收集全部人话错误，空=合法）：①needs 可达（must 数据流定点 ⊓=∩，处理环）②有 when 出边必有唯一 else ③去掉带 budget 的 router 后须无环 ④when 经 `check_cond` 静态校验；另含结构前置（id 唯一/use 注册/端点已知/可达/END 可达）。`recipes_cond.py` 加 `check_cond`（静态结构校验）。
- 判据：`tests/test_validate.py`（7 条）好图过 + 断前置/缺 else/无闸环/坏 when/未知节点/未注册各报对应错；`.venv` 全量 **139 passed, 2 skipped**。

**S5.4.1d 三配方改写成 JSON 等价替换 ✅**
- 做：新增 `app/recipes_builtin.py`（FANOUT/ROUNDTABLE/ROUNDTABLE_CONTINUOUS/AUTO 四份声明式 JSON）；`build_fanout/roundtable/auto_recipe` 改为组 deps（节点形参名，`clarify_assess→assess`）+ `compile_recipe(JSON)`，删手写拓扑，签名不变。**A3 端到端等价**。
- 附带修正 1c：环检查把 **human 节点也算闸**（interrupt 暂停等人、不自主空转），否则扇出 `curate→curate` 自循环误报。
- 判据：既有端到端（roundtable/fanout/auto/relay/service）**零改全绿** + 4 内置配方过 `validate`；`.venv` 全量 **143 passed, 2 skipped**。

> **S5.4.1 编译器（a–d）完成** ✅：`when` 解释器 + `compile_recipe` + `validate_recipe` + 三配方=数据。配方真正变成可校验/可编译/可跑的 JSON。下一组 S5.4.2 配方库（存储 + 跑任意库内 DAG）。

### S5.4.2 配方库（存储 + 运行任意库内 DAG）

**S5.4.2a recipe 表 + CRUD ✅**
- 做：`db/models.py` 加 `Recipe(id,name,builtin,graph:JSON)`；`db/repo.py` 加 `seed_builtin_recipes`（四内置幂等 seed）；`/recipes` CRUD（写时 `validate_recipe` 校验、内置不可删/改）；lifespan 接 seed。
- 判据：`tests/test_recipe_crud.py`（4 条）seed/自定义 CRUD/内置只读/拒坏图；`.venv` 全量 **147 passed, 2 skipped**。

**S5.4.2b `/recipe/run` 跑库内 DAG ✅**
- 做：create_app 加 `planner` 形参 + 存 `app.state.saver`/`recipe_deps`（live deps）；`POST /recipe/run {recipe_id,group_key,request,roster}`——取 graph→`validate`→`compile_recipe(deps)`→`_sse_from_events` 流式；server.py 接 `default_planner`（生产可跑 auto）。
- 判据：`tests/service/test_recipe_run.py`（3 条）内置 roundtable_continuous 跑到 output + auto(假 planner)跑到 output + 未知 id→404；`.venv` 全量 **150 passed, 2 skipped**。

> **S5.4.2 配方库（a–b）完成** ✅：配方可存库/校验/分享 + 跑库内任意 DAG。L3（S5.5）与画布（S5.4.3）都复用 `/recipe/run` 这条管线。

### S5.4.3 卡片流画布（L4 对运营的门面）

**S5.4.3a 原语→卡片库投影 API ✅**
- 做：`GET /primitives` 返回 registry 每原语机读契约（name/kind/reads/writes/needs/emits/budget/args）；`_primitive_dict` 投影。
- 判据：`tests/service/test_primitives.py`（1 条）9 原语全在 + kind/needs/emits/budget 字段对；`.venv` 全量 **151 passed, 2 skipped**。

**S5.4.3b 只读渲染：DAG JSON → 竖向卡片流 ✅**（呼应"AI 搭的图也要看得懂"）
- 做：`components/RecipeFlow.vue`（DFS 前序排竖向流；原语人话名 + kind 色条/chip + budget 闸徽标；出边逐条人话标注条件/循环↻）+ `views/RecipesPage.vue`（左列库内配方、右渲选中图，只读）+ api `listPrimitives/listRecipes/getRecipe` + 路由 `/recipes` + 导航「配方」。
- 判据：`npm run build` 过（717 模块）；三内置配方渲染成可读卡片流（手动眼检）。

**S5.4.3c 编辑：增删卡 + 改出边 + 实时校验 ✅**
- 做：后端 `POST /recipe/validate{graph}→{errors}`（复用 1c，不落库）；前端 `RecipeEditor.vue`——加卡（原语库 chip）、删卡、改出边（目标下拉 + 条件下拉只暴露 router/human 的 emits 人话/「否则」，不露裸 when）、去抖实时校验标红、存库（create/update）；`RecipesPage` 接「复制为草稿/编辑/新建」+ `onSaved`；`humanizeWhen` 等抽到 `utils/recipeLabels.js` 与 RecipeFlow 共用。
- 不做：节点级 args（spec.args 全 None）；通用 when 的裸 field/op/value 编辑（留高级模式）。
- 判据：`npm run build` 过；后端 `tests/recipes/test_recipe_crud.py::test_validate_endpoint`；`.venv` 全量 **152 passed, 2 skipped**；改图存库再渲染（手动）。

**S5.4.3d 三模板可改 + 存为新配方 ✅**
- 做：模板可改+另存（3c 的「复制为草稿→存库」已覆盖）；api `recipeRunStream`；`ChatPage` 支持 `?recipe=id`（用 `/recipe/run` 起场、续场仍走圆桌 resume，复用全部气泡/暂停逻辑）+ 配方 chip；`HomeView` 加「我的配方」区列库内自定义配方 → 选中 `goRecipe`→`/roundtable?recipe=id`。
- 判据：`npm run build` 过；从圆桌模板改出新配方→存库→首页可选→开场跑通（手动端到端）。

> **S5.4.3 卡片流画布（a–d）完成** ✅。**S5.4 配方升 L4 全部完成**：原语三态乐高 + 声明式 DAG 编译/校验 + 配方库 + 卡片流画布（看/编/存/跑）。下一步 S5.5：L3 让 AI 产出 DAG，复用这整条管线。

## S5.5 L3 真正通电：AI 产出 DAG（依赖 S5.4）✅

> 取代原 S5.3。L3 = planner 不运行时即兴，而是**产出一张 recipe DAG 工件**，复用 S5.4 编译器跑、画布渲成卡片流、可存可改。§B2：AI 只做结构化高层选择，确定性 assemble 拼出保证合法的图。
- 做：`recipes/plan_recipe.py`——`RecipePlan{mode,clarify,human_in_loop,reason}` + `default_recipe_planner`（LLM）+ `assemble_recipe`（据选择裁内置模板，恒合法）+ `plan_recipe(task,roster,*,planner)->(name,graph)`；`POST /recipe/auto {task,roster}` 产图→存库（builtin=False）→返回（可在画布看/改/跑）；create_app 加 `recipe_planner`，server 接 `default_recipe_planner`；首页「让 AI 搭一个配方」→ `/recipes?select=id`；配方页「▶ 运行此配方」。
- 不做：AI 裸写 nodes/edges（§B2 不允许）；AI 在画布上增量改图；更丰富的分阶段组合（assemble 可扩展）。
- 判据：`tests/recipes/test_plan_recipe.py`（5 条）圆桌/扇出/去clarify/无planner 各出合法图 + `/recipe/auto` 存库且经 `/recipe/run` 跑到 output；`.venv` 全量 **157 passed, 2 skipped**；`npm run build` 过。

> **L1→L4 全线打通**：L1 用户选 / L2 主持人荐现成 / L3 AI 搭新图 / L4 用户卡片流自拼——四层都落到同一套「声明式 DAG + 编译器 + 配方库 + 卡片流画布」管线。

---

## S5.6 分层圆桌（breakout 原语）🅿️ 设计中

> 子群领域圆桌 → 跨域圆桌（避免 8~10 agent 同群乱序）。需新增 `breakout` 原语（roster 分域 + 子图 map + 汇总上提）+ 状态嵌套 + 扩 `RecipePlan`/`assemble` 词表。当前引擎扁平、无子群（见 `docs/引擎能力与原语.md` §9），是真新能力，**暂不落细**（待单独设计）。

## S5.7 会话历史（复用 checkpointer，§6.17）

> state 已按 group_key 持久在 checkpointer；只缺"列出会话"的索引 + 渲染。**未到 END 的会话可续**（继续=重试 共用"按 recipe_id 取图续跑"，见 §6.17 命门）。不分配方（用户要求）。

**S5.7a 会话索引表 + 读端点 ✅**
- 做：`Conversation` 表（id=group_key, title, recipe_id, created_at, updated_at）；roundtable/`/recipe/run` 起场时 `upsert_conversation`；`GET /conversations`（按 created_at 近→远）；`GET /conversations/{key}`（`roundtable_graph.aget_state` 读 history/output/roster + `resumable`=snap.next 非空；读 state 与图无关）。
- 不做：消息另存表；前端。**`_graph_for` 挪到 S5.7b**（读 state 与图无关，续跑才需取对应图）。
- 判据：`tests/service/test_conversations.py`（3 条）列到/含发言 history+resumable/近→远序/未知 404；`.venv` 全量 **160 passed, 2 skipped**。

**S5.7b 历史页（渲染 + 继续未结束的）✅**
- 做（后端）：`_graph_for(app_state, recipe_id)`（空→roundtable_graph / 否则库内 recompile）+ `_resume_payload`（抽出 resume dict，roundtable resume 复用）；`POST /session/{key}/resume/stream`（按 `Conversation.recipe_id` 取图续场，自定义配方也能续）。
- 做（前端）：`HistoryPage`（`/history` 列对话→点开进 ChatPage `?conversation=key`）+ 导航「历史」；ChatPage 支持 `?conversation=key`（`getConversation` 载历史气泡/roster/output、不重生 group_key、`resumable`→显示继续/插话/结束窗口、隐藏起场表单）；resume 全改走通用 `sessionResumeStream`；api `listConversations/getConversation/sessionResumeStream`。
- 判据：`tests/service/test_conversations.py`（+2：/session resume 续默认圆桌 & 自定义配方）；`.venv` 全量 **162 passed, 2 skipped**；`npm run build` 过；历史页打开停在 human_gate 的会话→继续能接着跑（手动）。

> **S5.7 会话历史（a–b）完成** ✅：历史可列、可看、未结束可续（继续=按 recipe_id 取图在同 thread 续场）。出错重试 S5.8 复用同一 `_graph_for`。

## S5.8 出错重试（断点续跑，§6.17）

> 节点报错时 checkpointer 停在该节点前的最后成功超步；以 `None` 续跑 = 重试该节点（不整场重来）。复用 S5.7a 的 `_graph_for`。

**S5.8a 重试端点 ✅**
- 做：`POST /session/{key}/retry/stream`——`_graph_for(recipe_id)` 取图 → `_sse_from_events(graph, None, cfg)` 从最后 checkpoint 重跑挂起节点。**已 verify** LangGraph `astream(None)` 续跑语义。
- 不做：前端（S5.8b）。
- 判据：`tests/service/test_retry.py` — 假 generate 首调抛错→起场 SSE 出 error；retry→出 turn+human_gate；且 frame 的 assign 只跑 1 次（断点续证据）、generate 跑 2 次；`.venv` 全量 **164 passed, 2 skipped**。

**S5.8b 前端重试按钮 ✅**
- 做：ChatPage error（SSE error 或网络异常）+ 有 groupKey → `canRetry`；错误条上「重试」按钮 → `retry()` 清掉报错那轮半截气泡（`current`）→ `sessionRetryStream`（/session/{key}/retry/stream）续流；api `sessionRetryStream`。
- 判据：`npm run build` 过；人为造错后点重试能接着跑（手动）。

> **S5.8 出错重试（a–b）完成** ✅。**S5.7+S5.8**：历史可列/看/续/删 + 出错可重试，全建在 checkpointer + `_graph_for` 上。

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

## S7 好友双绑定解耦：身份中立 + 平台无关 + 模型无关（§6.18）

> 好友 = 中立身份 + 两条可插拔绑定（`channel` 出站 / `llm_ref` 推理）。编排核心只见 `contact_id` + 不透明 `group_key`；平台与模型选择收敛到边界两张表。两维度一起落档、分批切片。先模型（S7.1，本次主诉求），后平台（S7.2）；引用式注册表 + env 引用 key + 轻量 router（已定）。

### S7.1 每好友独立 LLM 后端（模型解耦）

**S7.1a LLMBackend 注册表 + CRUD（key 走 env 引用）✅**
- 做：`db/models.py` 加 `LLMBackend(id,name,base_url,api_key_env,model,temperature,max_tokens,created_at)`（不存明文 key）；`llm.py` 加 `make_chat_model_from_backend`（api_key 从 `api_key_env` 指向的环境变量读，缺失抛 `MissingApiKeyEnv` 含后端名+变量名）；`service.py` `LLMBackendIn`（不收明文 key）+ `/llm-backends` CRUD。
- 不做：接到 generate（S7.1b）；前端（S7.1c）。
- 判据：`tests/service/test_llm_backends.py`（3 条）CRUD（建/重409/列/改/改不存在404/删/再删404）+ 缺环境变量抛 MissingApiKeyEnv + 命中 env 造出 ChatOpenAI（key 取自环境、不落库）；`.venv` 全量 **167 passed, 2 skipped**（A3）。

**S7.1b `ModelProvider` + generate/turn 按好友取模型（带缓存）✅**
- 做：`generate.py` `ModelProvider=(contact_id)->ChatOpenAI|None`（对称 `PersonaProvider`）+ `default_generator(...,model_provider=None)`（`m=await model_provider(cid) or model`）；`turn`/`fanout` 加 `model_provider` 形参透传；`build_{fanout,roundtable,auto}_recipe` + `recipe_deps` 串 `model_provider`；`repo.model_provider_from(sf,*,cache)` 按 `Contact.llm_ref→LLMBackend→make_chat_model_from_backend` 造模型、**按 backend.id 缓存**、无绑定→None；`Contact.llm_ref` + `ContactIn.llm_ref` + `create_app(model_provider=...)` 默认 `_from(sf)`。
- 不做：前端选后端（S7.1c）；流式 chunk 路由变化（沿用现有 tags/metadata）。
- 判据：`tests/nodes/test_model_provider.py`（3 条）generate 按 contact 路由对模型 + 无绑定回退全局 + provider 绑定/缓存命中(同实例)/无绑定/坏 ref/不存在好友均回退 None；`.venv` 全量 **170 passed, 2 skipped**（A3 零回归）。

**S7.1c 前端：好友选 LLM 后端 + 后端管理页 ✅**
- 做：api `listLlmBackends/createLlmBackend/updateLlmBackend/deleteLlmBackend`；`LLMBackendsPage`（`/llm-backends`，导航「模型」）后端 CRUD，强调 api_key_env 填变量名非明文；`ContactsPage` 好友加「LLM 后端」`v-select`（绑 `llm_ref`，clearable，空=默认）+ 列表显示绑定模型名 + 并发拉 contacts/backends。
- 判据：`npm run build` 过（722 模块）；建两后端、ada1 绑 gpt / ada2 绑 deepseek，开圆桌各以对应模型发言（手动端到端，需真实 key 环境变量）。

> **S7.1 每好友独立 LLM（a-c）完成** ✅：LLMBackend 注册表（key 走 env 引用）+ ModelProvider 按好友取模型（缓存）+ 前端绑定/管理。`ada1=gpt、ada2=deepseek` 全线打通。

### S7.1 细化（学 AstrBot provider 流程，§6.18+）

> 现状裸填 base_url/model/api_key_env，用户判断不了对错；且好友若想用 AstrBot 已配好的 provider 无路。两点：配置可验证 + LLMBackend kind 化（AstrBot 当后端）。两项一起落档、分批切。

**S7.1d 配置可验证：测试端点 + 拉模型列表 ✅**
- 做（后端）：`llm.py` 抽 `resolve_api_key` + `ping_model`（打 `REPLY PONG ONLY`，单次不重试，超时）+ `probe_models`（httpx GET `{base_url}/models`）；`POST /llm-backends/test`（按表单值 make→ping→{ok,reply|error}，仿 check_one）；`POST /llm-backends/probe-models`（仿 model_list）。**都按表单当前值校验、可未落库；失败收 {ok:False,error} 不抛 5xx**。
- 做（前端）：后端表单「测试连通」按钮（绿/红 alert）；model 改 `v-combobox` + 「拉取模型」按钮（拉到选、拉不到回退手填）。
- 不做：base_url 预设、AstrBot 全类型模板（§6.18+ 已否决）。
- 判据：`tests/service/test_llm_backends.py`（+4）ping_model 假 model + test 端点 key 缺失/ping 成/败 + probe 列表/缺 key 兜底；`.venv` 全量 **174 passed, 2 skipped**（A3）；`npm run build` 过。

**S7.1e LLMBackend kind 化 + astrbot 委托后端 ✅**
- 做（schema）：`LLMBackend` 加 `kind`（默认 `openai`，兼容现有）+ `provider_id`；`llm_astrbot.make_model_from_backend(*,bridge_url)` 分流：openai→ChatOpenAI、astrbot→`AstrBotChatModel`（实现 astream，经桥 `POST /llm` 委托，MVP 非流式吐一 chunk，send 可注入）。
- 做（桥）：`group_relay/llm_bridge.do_llm(get_provider,payload)`（纯逻辑）+ main.py `POST /llm` → `Context.get_provider_by_id(id).text_chat(...)` → `completion_text`。
- 做（前端）：后端类型下拉（openai 显 base_url/key/model；astrbot 显 provider_id）+ `canTest` 按 kind。
- 不做：astrbot 流式（`text_chat_stream` 桥 SSE，后补）；anthropic/gemini 原生 kind（用到再加）。
- 判据：`tests/infra/test_llm_astrbot.py`（6）+ `test_model_provider.py`（+1）+ 插件 `test_llm_bridge.py`（3）；`.venv` 全量 **180 passed, 2 skipped**（A3）+ 插件 **17 passed**；`npm run build` 过；真 AstrBot 委托 smoke=手动。

> **S7.1 每好友独立 LLM（a-e）全部完成** ✅：注册表(env key)→ModelProvider(缓存)→前端→可验证(测试+拉模型)→kind 化(openai 自包含 / astrbot 委托)。下一组 **S7.3**（AstrBot 整 bot 引用，§6.18++「C」）；**S7.2 平台解耦缓做**（只有 AstrBot 一个平台、bot_ref 已能出站，等接第二个平台再做）。

### S7.2 平台解耦（channel 绑定 + OutboundClient router）🅿️ 缓做

> **缓做原因（2026-06-04）**：S7.2 是「送达通道」的平台无关 router，与「模型来源」(S7.1/S7.3) 正交。当前**只有 AstrBot 一个平台**、`bot_ref` 自 S4.3 已能精确出站——S7.2 这层通用 router 是纯重构、**在第二个 IM 平台出现前无用户可见回报**（同 S4.5 缓做、conversation_id 重方案"用到再回头"）。**何时回头**：真要接第二个平台（discord/微信/…）时，S7.2 + 该平台 driver 一起做（那时 router 抽象才有意义、才好验）。注意 S7.3 不依赖 S7.2——它复用现成 `bot_ref` 出站。

**S7.2a `Contact.channel{adapter,account_ref}`（兼容旧 bot_ref）**
- 做：`Contact` 加 `channel:JSON {adapter,account_ref}`；迁移/缺省把旧 `bot_ref`→`{adapter:"astrbot",account_ref:bot_ref}`（读时兼容，零数据丢失）；`ContactIn`/CRUD 透出 channel。
- 不做：router（S7.2b）；非 astrbot driver（用到再加）。
- 判据：`tests/` 旧 bot_ref 兼容读成 astrbot channel + channel 直写读对；`.venv` 全量绿（A3）。

**S7.2b OutboundClient 升级为 adapter router + group_key 重定义**
- 做：`ChannelDriver` 统一接口 `send(group_key,account_ref,text)`；`OutboundClient` 按 `contact.channel.adapter` 派给对应 driver（AstrBot 桥退化成 `astrbot` driver，行为不变）；文档把 `group_key` 重定义为 **transport 不透明令牌**（解释权归 adapter，编排层只透传）。
- 不做：第二个真实 IM driver（要接时单独一刀）；内部 conversation_id 映射层（重方案，留 §6.18「何时回头」）。
- 判据：`tests/transport/` astrbot driver 路由等价旧行为（既有出站测零改全绿，A3）；router 按 adapter 选对 driver、未知 adapter 报错。

### S7.3 AstrBot 整 bot 绑定（channel+llm 合一，§6.18++ 「C」）

> 好友 ≡ 一个 AstrBot bot：指一个引用同时拿「通道(以该 bot 发言)+模型(该 bot 在用 provider)」，省去分别配 channel/llm；Chorus 引擎（人设/维度/点账本/合成）原样保留。复用 S4.3 的 bot_ref（=channel）+ S7.1e 的桥 `/llm` + AstrBotChatModel。**否决 B 整 bot 自治（掏空引擎）。** 这类好友硬绑 AstrBot、不能单机——故 S7.1/S7.2 仍保留供单机与引擎自身 LLM。

**S7.3a 桥 `/llm` 支持按 bot/umo 取 using-provider ✅**
- 做：`llm_bridge.do_llm(get_provider_by_id, get_using_provider, payload)` 兼容两种入参——显式 `provider_id`（S7.1e）或 `umo`（取 `Context.get_using_provider(umo)`=该 bot 在该群用的 provider）；main.py `_handle_llm` 注入 `get_using_provider`。
- 不做：前端/绑定（S7.3b/c）。
- 判据：插件 `tests/test_llm_bridge.py`（by_id / by_umo / 两种 404 / 缺 prompt 或都缺 400）**18 passed**；orchestrator 全量 **181 passed, 2 skipped**（A3，未动 orchestrator）。

**S7.3b 「跟随我的 bot」模型绑定 ✅**
- 做：哨兵 `llm_ref="@bot"`（`FOLLOW_BOT_LLM_REF`）；`run_ctx.current_group_key` ContextVar（turn/fanout 注入 state.group_key）；`AstrBotChatModel` 双模式（provider_id | bot_ref，follow-bot 按 current_group_key 构造 `_bot_umo`=平台段换 bot_ref，桥按 umo 取 using-provider）；`model_provider_from` 对 `@bot`+有 bot_ref 返回 follow-bot model、无 bot_ref→None。
- 不做：前端（S7.3c）。
- 判据：`tests/infra/test_llm_astrbot.py`（_bot_umo / follow-bot by umo / 缺 gk 抛错 / 需 id|bot_ref）+ `test_model_provider.py`（@bot→model、无 bot_ref 回退、turn 注入 ctx）；`.venv` 全量 **186 passed, 2 skipped**（A3）。

**S7.3c 前端：好友绑 AstrBot bot 一键 ✅**
- 做：`ContactsPage`「LLM 后端」下拉首项「跟随我的 AstrBot bot（bot_ref）」(值=`@bot`)；`backendName`/`llmHint` 适配（选中提示模型+通道都用该 bot、未填 bot_ref 警告回退）。
- 判据：`npm run build` 过；选「跟随 bot」→ 圆桌发言经该 bot provider + 以该 bot 身份发出（手动 smoke，需 AstrBot 跑着）。

> **S7.3 AstrBot 整 bot 引用（a-c）完成** ✅：桥按 umo 取 using-provider + follow-bot 绑定(ContextVar 注入 group_key) + 前端一键。一个 bot_ref 同供通道+模型，Chorus 引擎(人设/维度/点账本/合成)原样保留。

### S7.4 AstrBot bot 作为 LLM 后端（去 Contact.bot_ref，完全统一，§6.18+++）

> S7.3 的进化：把 bot 从「Contact.bot_ref + 选 @bot」上移成 **LLM 后端注册表里一类条目**（每个 AstrBot bot = 一个后端）。好友只选一个后端 = 模型(该 bot provider)+通道(以该 bot 发言)；去掉好友页 bot_ref。复用 S7.3a/3b 机器，只换 bot_id 来源。**取代 @bot 哨兵。** 取舍（已确认）：通道与模型绑成一个选择，不再支持"该 bot 通道+自有模型"正交组合。

**S7.4a `kind="astrbot_bot"` 后端 + 模型解析 ✅**
- 做：`LLMBackend` 加 `bot_id`；`make_model_from_backend` 加 `astrbot_bot` 分流 → `AstrBotChatModel(bot_ref=bot_id)`（复用 S7.3b follow-bot）；`LLMBackendIn`/`Check` 加 `bot_id`；老库自动补列。
- 不做：通道 provider 改造（S7.4b）；前端（S7.4c）。
- 判据：`tests/infra/test_llm_astrbot.py`（dispatch astrbot_bot→bot_ref=bot_id）+ `test_model_provider.py`（好友绑 astrbot_bot→follow-bot model）；`.venv` 全量 **187 passed, 2 skipped**（A3）。

**S7.4b 通道 provider 从后端取 bot_id + 去 @bot ✅**
- 做：`repo._contact_bot_id`（llm_ref→astrbot_bot 后端 .bot_id，legacy 回退 Contact.bot_ref）；`bot_ref_provider_from`/`roster_provider_from` 改用它；删 `model_provider_from` 的 `@bot` 分支（被 astrbot_bot 取代）。
- 不做：前端（S7.4c）。
- 判据：`tests/nodes/test_model_provider.py::test_channel_providers_read_bot_id_from_backend`（后端 bot_id / legacy 兜底 / 无→不在 roster）；`.venv` 全量 **187 passed, 2 skipped**（A3，出站测兼容）。

**S7.4c 前端：后端注册表加「AstrBot bot」+ 好友去 bot_ref ✅**
- 做：`LLMBackendsPage` 类型加「AstrBot bot」(astrbot_bot，显 bot_id；canTest 不含它/subtitleOf/blank/edit)；`ContactsPage` 移除 bot_ref 输入 + 移除 @bot 选项（下拉直接含各 AstrBot bot 后端；bot_ref 仍携带不抹 legacy）；删 dead `FOLLOW_BOT_LLM_REF`。
- 判据：`npm run build` 过；`.venv` 全量 **187 passed, 2 skipped**；建 AstrBot bot 后端→好友选它→圆桌经该 bot provider+以该 bot 发出（手动 smoke）。

> **S7.4 AstrBot bot 作为 LLM 后端（a-c）完成** ✅：好友的"模型来源"统一成一个菜单（openai 后端 / 各 AstrBot bot），选 bot 即模型+通道合一、好友页无 bot_ref。**S7 当前**：S7.1（每好友独立 LLM）✅ + S7.4（AstrBot bot 作后端，取代 S7.3 @bot）✅；S7.2（多平台 channel router）缓做。可选 S7.4d（从桥拉 bot 列表一键导入）。

**S7.4d（可选）从桥拉取 platform 实例一键导入 ✅**
- 做：桥 `llm_bridge.do_bots` + `GET /bots`（列 platform 实例 id/name，由 meta().id/adapter_display_name 构造）；`llm_astrbot.fetch_astrbot_bots` + orchestrator `GET /astrbot/bots` 代理；后端页「从 AstrBot 导入 bot」按钮批量建 astrbot_bot 后端（按 bot_id 去重）+ 修正过时文案。
- 判据：插件 `tests/test_llm_bridge.py`（do_bots 列表/异常）+ `tests/service/test_llm_backends.py`（/astrbot/bots 代理/桥不可达）；`.venv` 全量 **188 passed, 2 skipped** + 插件 **20 passed**；`npm run build` 过。

> **S7.4 完成后**：好友的"模型来源"= 一个统一菜单（openai 后端 / 各 AstrBot bot），选 bot 即模型+通道合一、无 bot_ref。**S7「好友双绑定解耦」**：S7.1（每好友独立 LLM）✅ + S7.3→S7.4（AstrBot bot 作后端）；S7.2（多平台 channel router）缓做（接第二平台再做）。

---

## S8 圆桌"结束"权归人（主持人建议、人拍板，§6.19）

> 人在环圆桌里主持人擅自结束（schedule moderator-stop 直奔 synthesize、绕过人）。改为：AI 的结束判断是建议→交人定；预算触顶让位给人而非结束。auto(continuous) 配方保持 AI 自动结束。

**S8a 引擎+配方：moderator-stop/budget → human_gate ✅**
- 做（配方，实现取舍）：让**配方**决定 stop 去向、`decide_next` 不动（更小改面、不破 schedule 测）——`ROUNDTABLE`（人在环）加边 `when stop_reason==moderator → human_gate` / `==budget → human_gate`（else 其它 stop 才 synthesize）。continuous 不变（budget 仍 →synthesize）。
- 做（引擎）：`human_gate` interrupt 带 `reason`；continue 清 `stop_reason`；`reason=="budget"` 也重置 `turns_since_human=0`（防死循环）。人在环每轮过 gate=人始终掌控，未加额外硬上限（recursion_limit 兜底）。
- 判据：`tests/recipes/test_human_gate.py`（moderator-stop→human_gate/不擅自合成/人 end 才收尾；budget→human_gate+续后 turns 归零不死循环）；schedule/roundtable(continuous)/compile 零改全绿（A3）。

**S8b 前端：human_gate 提示带原因 ✅**
- 做：ChatPage human_gate 事件读 `e.reason`→`pauseReason`；`gatePrompt` 按 moderator/budget/null 显不同提示（reason 非空标黄），按钮仍 插话/继续/结束。后端 Interrupt 已透传 reason（S8a），纯前端。
- 判据：`npm run build` 过；主持人建议结束/预算触顶时窗口显对应提示、点继续能接着聊（手动）。

> **S8 圆桌结束权归人（a-b）完成** ✅：AI 的结束判断=建议，人最终拍板（moderator/budget stop→human_gate + 前端显因）；relay 无真人自动收尾；continuous 配方仍 AI 自动结束。

---

## S9 圆桌 @定向插话（@某人=只让他改，不@=对全员，§6.20）

> 人在环圆桌 `@ada1 改X` 只让 ada1 改、别人不插嘴；不@ 维持现状（主持人挑谁接）。多@批量按序跑完再回到人；修订追加、合成偏最新。@只在两轮间生效（不打断流式）。

**S9a 引擎+配方：directed_queue + 定向调度 + 批量不连锁**
- 做（state）：`GroupState` 加 `directed_queue:list[str]`（+ 一个 directed 标记供 turn 框架）。
- 做（节点）：`human_gate` 解析 interject 的 @目标 → 填 `directed_queue`（指令文本仍进 history）；`schedule` 优先级最前 `directed_queue` 非空 → pop → `NextSpeaker(directed)`（跳过主持人/预算）；`turn` 定向时 prompt 框架为"真人点名要你按〈指令〉修改"。
- 做（配方）：`ROUNDTABLE` 加边 `turn when directed_queue 非空 → schedule / else → human_gate`（批量跑完再停、不连锁）。
- 不做：合成去重（S9b）；前端 @ chips（S9c）；打断流式 barge-in。
- 判据：`tests/` @单人→只他发言→回 human_gate（主持人不接力）；@多人→按序全发完才停；不@→维持主持人挑人；定向跳过预算闸；`.venv` 全量绿（A3）。

**S9b 合成/点账本：修订追加偏最新 + 旧主张去重**
- 做：被@者修订作新发言追加；`synthesize`/claims 投影偏该 speaker 最新一版、旧版去重（§6.11）。
- 判据：`tests/` 同一 speaker 修订后合成用新版、不双算旧主张；`.venv` 全量绿。

**S9c 前端：@ chips + 定向轮渲染**
- 做：ChatPage 插话框加 roster 成员 @ chips（选谁→@其 contact_id）；定向发言气泡标"应 @ 修订"。
- 判据：`npm run build` 过；@某人只他改、不@对全员（手动）。

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
S2.4(Contact+出站) ─→ S7.1(每好友独立 LLM: 注册表→ModelProvider→前端) ; S7.2(平台解耦: channel→router) 独立于 S7.1（§6.18，两维度并行可切）
S7.1e + S4.3(bot_ref) ─→ S7.3(AstrBot 整 bot 绑定: channel+llm 合一，§6.18++「C」；桥按 umo 取 using-provider→跟随 bot→前端)
S7.3 ─→ S7.4(AstrBot bot 作 LLM 后端，去 Contact.bot_ref，§6.18+++；kind=astrbot_bot→通道 provider 取 bot_id→前端去 bot_ref→可选导入)
```

**三个关键验收点**：
1. **S1.6 / S1.8**：扇出策展端到端跑通 = 编辑场景 MVP 价值落地。
2. **S3.3**：用已有原语零改引擎拼出第二张配方 = §6.6 配方抽象成立。
3. **S4.4**：Telegram 真实多 bot 多身份 = 平台适配验证（迁 QQ 前的护栏）。

**落盘原则**：每刀完成当场 commit + 跑判据 + 回本文勾掉对应行；下一会话从文件状态接手，不依赖上下文记忆。
