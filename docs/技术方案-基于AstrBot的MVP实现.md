# 技术方案：基于 AstrBot 的「AI 微信群」MVP 实现

> 配套需求文档：[方案-AI微信群-需求与技术方案.md](方案-AI微信群-需求与技术方案.md)。
> 本文把该需求的 **MVP 地基**落到一个具体技术底座上：**AstrBot 当瘦多 bot 适配层 + 独立 LangGraph 编排服务当大脑**。
> 范围严格限定在需求文档 §6 步骤 1-4（不含 §7 层级 / §8 成长 / §9 执行层）。状态：方案阶段，未写代码。

---

## 0. 结论先行

- **底座分工**：AstrBot 只做"N 个独立 bot 的收发消息"，不参与编排、不调 LLM；编排大脑是独立 LangGraph 服务，二者用一条窄消息桥解耦。
- **为什么这么分**：AstrBot 原生支持 N 个独立 bot 身份（`config["platform"]` 是列表，`PlatformManager.initialize` 逐个实例化，`manager.py:87-100`），且自带 Telegram/QQ 官方适配、`send_by_session` 主动推送（`platform.py:133`）、SQLite 持久化——正好白送需求里"多身份 + 合规 + 主动 push"这几块；编排核心（§5.5 调度循环）AstrBot 没有，交给 LangGraph。
- **MVP 覆盖**：调度状态机（§5.5）、混合身份（§5.4）、N bot 推送（§5.3）、人在环打断、预算闸、数据模型（好友/群/消息/短期记忆）。

---

## 1. 架构骨架

```
┌─────────────────────────────────────────────────────────┐
│            编排服务 (独立进程, Python + LangGraph)          │  ← 平台无关
│  ┌─────────────────────────────────────────────────┐    │
│  │ LangGraph StateGraph: 群聊调度状态机              │    │
│  │   moderator_framing → agent_turn → schedule(决策) │    │
│  │   checkpointer(可中断/可续) + 打断队列            │    │
│  └─────────────────────────────────────────────────┘    │
│  Contact注册表 · Group模型 · 短期记忆 · LLM(LangChain)    │
└───────────────▲───────────────────────┬─────────────────┘
       入站(群消息JSON)            出站(bot_id, session, 文本)
       POST /inbound                 POST → AstrBot 插件
                │                         │
┌───────────────┴─────────────────────────▼─────────────────┐
│              AstrBot (瘦适配层, 单进程)                      │
│  group_relay 插件(Star):                                   │
│   · 入站: 群消息 → 去重 → 转发编排服务 → stop_event()       │
│   · 出站: 收编排指令 → 选中 bot X 实例 → send_by_session    │
│  PlatformManager: N 个 platform 实例 = N 个 bot 身份         │
│   ├ telegram#老陈(token A)  ├ telegram#小杨(token B)  …      │
└────────────────────────────────────────────────────────────┘
```

**职责红线**：LLM 调用、人设拼接、调度决策、记忆——**全在编排服务**。AstrBot 侧 `group_relay` 插件只搬运字节，不含任何业务智能。这样 §12.4 的"调度器换框架"天然成立，将来换 AutoGen 只动编排服务。

---

## 2. 桥接协议（窄接口，是解耦的命门）

### 2.1 入站：群消息 → 编排服务

AstrBot 侧 `group_relay` 插件（一个 `Star`）注册群消息钩子，把每条群消息规范化后转发：

```python
# 规范化消息（编排服务的唯一入站契约）
class InboundMsg(BaseModel):
    group_key: str        # AstrBot unified_msg_origin, 唯一标识一个群
    platform: str         # "telegram" | "qq_official" | ...
    sender_id: str
    sender_name: str
    sender_kind: Literal["human", "ai"]   # 区分人类还是某 AI bot
    text: str
    native_msg_id: str    # 平台原生消息 id, 用于去重
    ts: float
```

插件伪代码：

```
on_group_message(event):
    if seen(event.group_key, event.native_msg_id):   # 去重: N 个 bot 都会收到同一条
        event.stop_event(); return
    mark_seen(...)
    POST orchestration "/inbound" with InboundMsg(...)
    event.stop_event()        # 关键: 阻止 AstrBot 默认 LLM 流水线自动回复
```

> **两个坑必须处理**：① N 个 bot 在同群各收到一份人类消息 → 按 `(group_key, native_msg_id)` 去重，先到先得。② 不 `stop_event()` 的话 AstrBot 会用自己的 provider 自动回复，与编排服务打架 → 必须截断。

### 2.2 出站：编排服务 → 以 bot X 身份发言

编排服务决定"老陈说 X"后，调 AstrBot 插件暴露的本地 HTTP 端点：

```python
class OutboundCmd(BaseModel):
    group_key: str
    bot_id: str           # 对应某个 AstrBot platform 实例的 id (= Contact.bot_ref)
    text: str
```

插件处理：

```
on_outbound(cmd):
    platform_inst = platform_manager.find_by_id(cmd.bot_id)   # 选中那个 bot 的实例
    session = MessageSession.from_unified_origin(cmd.group_key)
    await platform_inst.send_by_session(session, MessageChain([Plain(cmd.text)]))
    # 群里就显示是 bot X 这个号在说话
```

`send_by_session`（`platform.py:133`）正是为"不持有 event 对象也能主动发"设计的，天然契合调度器异步 push。

---

## 3. 编排核心：配方引擎（以「圆桌」配方为例）

> 引擎本身不内置模式，只运行**配方**（核心抽象见 §10）。本节把"圆桌"这张配方实例化出来当样板；扇出策展是另一张配方，复用同一组原语与 `GroupState`。

### 3.1 圆桌配方的状态机

```
IDLE ──human_msg──▶ CLARIFY(主持人自评对需求的信心):
        ├─ 信心够 ──────────────▶ FRAMING                  # 快路, 不打扰
        └─ 信心不足 ─▶ YIELD(回述+至多一问) ─▶ 等用户 ─▶ FRAMING
        (用户可"跳过澄清"强制进 FRAMING)
FRAMING(主持人分维度+点名 roster) ──▶ AGENT_TURN(生成一个 agent 发言, 推送)
AGENT_TURN ──▶ SCHEDULE(决策):
        ├─ next_speaker(agent_id, dim) ─▶ AGENT_TURN
        ├─ yield_to_human(prompt"继续/总结?") ─▶ IDLE
        └─ stop(reason: 预算到顶) ─▶ IDLE
人类消息可在任意状态注入 → 进入 INTERRUPT，重走 CLARIFY/FRAMING/SCHEDULE
```

> **CLARIFY 子态（需求澄清，强度档位 B，见 §6.5）**：`framing` 前加一次廉价 LLM 自评"我对这个需求的理解够不够派活？"，与 `decide_next` 同构。信心不足才回述 + 问**最关键的一个**问题，一次一问、可跳过——克制是命门，阈值调太高退化成问卷(C)，太低退化成跑偏(A)。MVP 用固定保守阈值，自适应留 Phase 2。

### 3.2 状态与决策（精确签名）

```python
class GroupState(BaseModel):
    group_key: str
    roster: list[AgentSlot]          # 本场到场好友 + 各自被分配的维度
    history: list[InboundMsg]        # 短期记忆 = 群历史(MVP)
    turns_since_human: int           # 预算闸计数
    max_turns_per_human: int = 6     # 预算闸阈值(可配)
    pending_human: InboundMsg | None # 打断队列: 讨论中人类插话落这里

class AgentSlot(BaseModel):
    contact_id: str
    dimension: str | None            # 临场维度(每场可变)

# 调度决策: 一个 union, 由一次廉价 LLM 调用产出
SchedulerDecision = NextSpeaker | YieldToHuman | Stop

def decide_next(state: GroupState) -> SchedulerDecision:
    if state.pending_human:  return YieldToHuman(...)        # 人插话优先
    if state.turns_since_human >= state.max_turns_per_human:
        return Stop(reason="budget")                          # 预算闸
    return moderator_llm_pick(state)   # 主持人 LLM: 选下一个谁说 or 让位/停
```

### 3.3 LangGraph 节点与边

| 节点 | 做什么 |
|---|---|
| `framing` | 新问题进来：主持人 LLM 读问题 → 分维度 → 给 roster 每人 `dimension` |
| `agent_turn` | 取下一发言人：拼人设(§4) → LLM 生成发言 → 出站推送 → `history` 追加 → `turns_since_human += 1` |
| `schedule` | 调 `decide_next`，按返回类型走条件边 |

- **可中断/可续**：用 LangGraph `checkpointer`（如 SqliteSaver）持久化 `GroupState`，进程重启可续；人类打断写入 `pending_human`，`schedule` 每步先查。
- **MVP 调度策略**：主持人每步一次廉价 LLM 调用（需求 §5.5 选定），可控好调试；抢麦/竞价留 Phase 2。

---

## 4. 混合身份模型落地（§5.4）

```
Contact(持久) = 基础人设(名字/头衔/风格/底层立场) + bot_ref(指向某 AstrBot bot 实例)
进群一场 = 主持人给每个 Contact 叠加 临场维度(dimension)
注入 prompt = 基础人设 + "\n本场维度: " + dimension + "\n群历史: " + recent(history)
```

同一个"经济顾问老陈"，身份稳定但每场维度可不同。LLM 调用在编排服务（LangChain `ChatOpenAI(base_url=.../v1)`），不经 AstrBot provider。

---

## 5. 数据模型（持久层, MVP）

```python
class Contact(SQLModel):     # 好友注册表
    id: str; name: str; title: str
    persona_style: str; base_stance: str
    bot_ref: str             # = AstrBot platform 实例 id, 出站时据此选 bot
    created_at: float

class Group(SQLModel):       # 群
    id: str; platform: str; group_key: str
    topic: str | None; state: str         # "idle" | "discussing"
    member_ids: list[str]; created_at: float

class Message(SQLModel):     # 群历史(=短期记忆)
    id: str; group_key: str
    sender_id: str; sender_kind: str      # human|ai|moderator
    text: str; dimension: str | None; ts: float
```

> **记忆深度（需求 §10.2 待定）**：MVP 只做"本群短期历史"（按 `group_key` 取最近 N 条 / token 预算）。好友跨群长期记忆 + 检索留到后面。

---

## 6. 关键决策落档

### §6.1 集成模型
**决策**：AstrBot 仅作多 bot 适配层，LangGraph 独立编排服务当大脑。
**否决**：
- 插件内嵌：编排塞进 AstrBot 进程，受其 pipeline 模型束缚，换框架难。
- Fork core：维护成本高、跟不上上游。
**何时回头**：若桥的延迟/复杂度超过收益，再评估内嵌。

### §6.2 桥接与去重
**决策**：转发 Star + `send_by_session` 出站；按平台 msg_id 去重；`stop_event` 防自动回复。
**命门**：漏 `stop_event` → AstrBot 自带 LLM 抢答；漏去重 → N bot 重复触发。

### §6.3 调度策略
**决策**：主持人每步一次廉价 LLM 调用决定下一个谁说/停（MVP）。
**否决**：轮转(机械)；抢麦竞价(贵且易乱，留 Phase 2)。

### §6.4 LLM 归属
**决策**：LLM 调用全在编排服务（LangChain），AstrBot 不调 LLM。
**否决**：复用 AstrBot provider —— 会把智能漏进适配层，破坏解耦。

### §6.5 需求澄清强度
**决策**：主持人=需求单一入口；信心触发的轻澄清（回述+至多一问、可跳过），落为 `CLARIFY` 子态。
**否决**：
- A 零澄清直发：模糊需求→维度乱分→跑偏烧 token（§3.2 风险源头）。
- C 重澄清面谈：像填问卷，违背群聊零学习成本心智。
**命门**：阈值要克制——太高退化成 C，太低退化成 A。
**何时回头**：阈值改自适应（按用户是否点过"跳过"）时，Phase 2。

### §6.6 协作模式抽象
**决策**：模式不写死进引擎；引擎只提供原语，模式=可组合配方(recipe)，生产者维护配方库。MVP 弹性**行为**做到 L1（用户选配方），但原语/`GroupState` 设计**预留** L3（用户自拼）接口。
**否决**：
- 写死单一模式：不同用户工作方式不同，焊死是错误抽象层级。
- MVP 两模式都全做：体量翻倍、不必。
**命门**：原语边界要正交——拼新配方零改引擎才算抽象成立（= §7 的验收判据）。
**何时回头**：要开放 L3（DSL/可视化自拼）或 L2（主持人荐配方）时。

### §6.7 执行层复用（Phase 2，出 MVP 范围）
**决策**：沙盒复用 AstrBot（经同一座桥暴露 `/exec` 端点，零抽取）；skill 随沙盒白送；MCP 大脑自持、仅沙盒内工具随沙盒走。AstrBot 升级为"脸+手"。详见 §11。
**否决**：
- 大脑全自建沙盒/MCP：重写 §9 最难的隔离+安全工程，浪费。
- MCP 也绕 AstrBot：标准协议白加一跳，工具决策该和推理在一起。
**命门**：`computer_client` 耦合 `star.context.Context`，"插件内暴露 `/exec`"未实测，真做前先验。
**何时回头**：进入需求 §9 执行层 Phase 2 时。

### §6.8 前端：精简骨架 + 按需移植 AstrBot 外壳件（2026-06-01 修订）
**决策**：**不整包 fork** dashboard；建精简 Vue3+Vuetify 产品骨架（`web/`），**按需**移植 AstrBot 的核心外壳件（流式 markdown 气泡 / JWT 鉴权 / 主题）到真正用到的切片。产品域(群视图/策展页/配方选择)新建连大脑；管理域用到时再移植对应页。
**否决**：
- 整包 fork（原决策）：实测 AstrBot dashboard 的 `main.ts` 拖进 monaco/charts/tiptap 等 Chorus 不需要的死重量，且半剥离的重模板难 build。
- 从零全写、连外壳也不借：浪费 AstrBot 现成的流式气泡/鉴权。
**命门**：只移植真正用到的件；双后端(adminApi/brainApi)按需接。
**何时回头**：若移植件越积越多近似整个 dashboard，再考虑直接 fork。

### §6.9 结构化输出策略
**决策**：抽 `structured_invoke(model, msgs, schema, *, method)` 三策略（json_schema / function_calling / text_json）；method 由**每模型配置**决定，`text_json` 为通用兜底。调用点（frame/schedule）模型无关，接新模型=改配置。
**否决**：
- 把 text_json 写死进各节点：对支持原生结构化输出的模型过拟合到最低水位，且 SCHEDULE 会重抄解析。
- 运行时探测能力：不确定、不可复现；改用显式 per-model 配置。
**命门**：当前后端 deepseek-v4-pro 既不支持 `response_format` 也不支持强制 `tool_choice`（实测 400），故默认必须 `text_json`；json_schema/function_calling 分支**未对支持的真实模型 smoke 过**，接入时再验。
**何时回头**：接入第二个模型（如 OpenAI）时，给其配 `json_schema` 并 smoke。

### §6.10 原语分类与人在环（模型 A）
**决策**：原语分两类——**自包含**（`state→delta`，无需图外输入：clarify/frame/fanout/turn/schedule/synthesize）与**人在环**（需图外人的输入：curate）。**两类都进图**：人在环用 LangGraph `interrupt`（暂停—等人—`resume`，多轮用循环）表达。扇出配方 = 一张图 `clarify→frame→fanout→[interrupt: curate 循环]→synthesize`；service 层只剩"起/恢复图 + 转发 interrupt payload"，不再持有业务步骤。圆桌的人在环打断（S3.4）复用同一 interrupt 机制。
**否决**：
- 模型 B（人在环留 service 层操作）：synthesize 等自包含原语没进图，抽象不纯；且扇出(service-op)与圆桌(interrupt)是两套人在环路径，不统一。
**命门**：interrupt + 多轮 curation 的循环要处理对；HTTP 契约（`/inbound` 跑到 interrupt 返回候选、`/curate` 用 `resume`）要对前端兼容。
**何时回头**：若 interrupt 复杂度反噬，重评。
**迁移**：S1.6 的扇出现以模型 B 实现（curate/synthesize 为 service 操作）；切片 **S3.0** 迁到 interrupt 化，行为不变、测试保持绿（A3 重构）。

### §6.11 圆桌记忆：点账本压缩 + context 投影器（分辨率随距离衰减）
**决策**：圆桌长讨论的上下文用**分层记忆**——把发言压成**带归属的"点"**（claim ledger），而非叙述摘要。
- **数据**：`Claim{speaker_id, text, turn}`（一句话坚实主张 + 谁说的 + 第几轮），累积进 `GroupState.claims`，进 checkpoint、永不丢。第一版不含状态/对立关系（留后续）。
- **压缩单位 = 立场/点（非摘要）**：圆桌的意义在观点碰撞/继承/反驳，需保留"谁主张了什么"的归属与对立结构；叙述摘要会把多人观点揉成一团、丢归属，等于把圆桌压成会议纪要。**"点"复用 curate 已有的 point 抽象**（pick/reassign 操作的就是点）——两条配方共享同一"点"基础设施。
- **提点 = 发言后中立提取**：发言**自然流式**产出（SSE 不破）→ 完成后对该文本做一次轻量 `structured_invoke`（text_json，§6.9）提 1-3 个点。**中立提取器**（非发言者自总结）→ 点是客观主张、无"王婆卖瓜"；低温小模型即可。
- **context 投影器**：把 `generate.py` 写死的 `history[-10:]` 抽成可插拔 `build_context(history, claims)->messages`，默认实现 = **远场全部 claims（带归属、极精简）+ 近场最近 K 轮原文（K≈2-3，可配）**。"能看多远" = 全程可见但**分辨率随距离衰减**（近处高清原文、远处缩略成点）。窗口/摘要策略都成为它的另一种实现。
**否决**：
- 固定原文窗口（仅调大 N）：token 随轮数线性爆炸 + 悬崖式失忆（早期共识突然消失），治标。
- 叙述摘要压缩：丢归属与对立，碰撞结构没了。
- 发言时一并产出 `{speech, claims}`：结构化 JSON 无法逐 token 流出（破坏 SSE 打字体验），且 reasoning 模型强制结构化不稳（deepseek 坑）。
**命门**：提点质量 = 圆桌质量（压缩有损，丢 nuance 会"接错话"）；每轮多一次提取调用（成本/延迟，靠小模型+低温摊薄）。
**何时回头**：点提取质量不足时，引入点的状态/对立关系图（第二层）或可配的近场 K / 提取模型。
**落地**：圆桌组装（S3.3）前点账本须就位——**S3.1b**（账本基座+投影器抽象，claims 空时退化为原文窗口）→ **S3.1c**（中立提取原语 + TURN 集成提点）→ S3.2/S3.3。

### §6.12 transport / runtime 分层（统一驱动）
**决策**：抽 transport 无关的 `SessionRuntime` + 中性事件；web/telegram/CLI 都退化为 adapter。
**否决**：
- web 与 telegram 各写一套驱动（现状 S3.6/S4.4）：同一张圆桌图被两份驱动 + 两份出站映射（`_to_roundtable_event` vs `RelayDriver._push_new`）驱动，每加一种适配就再抄一份。
- 继续在 service 端点里直接驱动图：transport 细节（SSE/HTTP/桥）渗进核心。
**命门**：定准中性事件——入站 `Start(session_id,task,roster) | HumanMsg(session_id,text)`，出站 `Turn(identity_id,text,dim) | Ask(q) | Result(text) | Status(stage) | Done(reason)`；`group_key`/`bot_ref` 在 adapter 边界规范化成中性 `session_id`/`identity_id`（不让 AstrBot/telegram 概念进核心）。
**何时回头**：接第三种 transport（CLI/QQ），或上 §6.13 的 L2/L3 前——这是其地基。
**展开**：切片 S5.0。

### §6.13 配方分层 L1→L3（主持人按任务组原语）
**决策**：配方 = 主持人在原语集上的策略，分 L1 用户选 / L2 主持人荐 / L3 主持人逐步组原语；L3 用 §B2 框定。
**否决**：
- 一步到 L3 取代静态配方：LLM 动态流程难穷举测、易跑偏/死循环/烧钱。
- 永远停在 L1：无法按任务自适应（用户诉求）。
**命门**：L3 安全性靠 §B2——主持人 LLM 只"选下一个原语"（输入/建议），原语执行 + 预算/步数闸是确定性裁决；`decide_next` 从 `NextSpeaker|Yield|Stop` 泛化到 `Fanout|Speak|Curate|AskHuman|Synthesize|Stop`，"圆桌"/"扇出"退化为该策略的特例；静态配方留作兜底。
**何时回头**：§6.12 runtime 分层就位后——先做 L2（在已测配方库里选），再把 L3 做成库里一个带闸的 "auto" 配方。承接 §6.6（L1 已做、L3 预留）。
**展开**：切片 S5.1（L2）/ S5.2（L3）。

### §6.14 bot 实例管理归属（S4.5 缓做）
**决策**：N bot 实例的增删启停用 AstrBot 自带 dashboard，**不搬进 Chorus 产品 web**（S4.5 缓做）。
**否决**：
- 把 `PlatformPage` 搬进 Chorus web 连 `adminApi`：把产品 UI 重新耦合回 AstrBot 管理接口（违 §6.12 transport 无关），属管理域、当前低产品价值（§6.8 用到再移）。AstrBot dashboard 已实测够用（配 ada1/ada2 即用此）。
**命门**：管理域 / 产品域分治——产品 web 只碰 `brainApi`(core)，AstrBot 概念不渗进产品 UI。
**何时回头**：要做**统一运维台**（Chorus web 管一切、对运维藏掉 AstrBot）时。
**展开**：切片 S4.5（缓做）。

### §6.15 发布形态（pip 安装即用）
**决策**：core+web 打成一个 pip app（`pipx install chorus` → `chorus serve` 出完整 UI）；telegram 桥外置。
**否决**：
- 全套（含 telegram）塞进 pip：需 AstrBot 进程 / Node / 平台账号，pip 装不了——全套用 docker-compose / quickstart。
- 只发引擎库（`from chorus import ...`）：是库非产品，装了不能直接用。
**命门**：必须先把 LLM 配置从 `talk-agent/.env`（`CHORUS_DOTENV` 硬编码）解耦成标准环境变量（`CHORUS_LLM_BASE_URL/API_KEY/MODEL`），否则别人装了跑不起来。§6.12 已让 core 与 transport 解耦——故 pip 包 = core+web，telegram 天然在包外（分层必然，非缺陷）。
**何时回头**：要"全套一键"时上 docker-compose；telegram 想随包带时另议（仍进不了 pip）。
**展开**：切片 S6.0（配置解耦+包骨架+CLI）/ S6.1（打进前端 dist）/ S6.2（PyPI 发布 + group_relay 独立分发）。

### §6.16 配方分层到 L4（用户自造 DAG + L3 与 L4 归一）
**决策**：原语收敛成带 `PrimitiveSpec` 的三态乐高，配方=图原生 `nodes/edges` 声明式 DAG（用户自造）；L3=AI 填同一张 DAG，二者都在卡片流画布上呈现为同一工作流。
**否决**：
- 让用户写 Python 配方：非技术运营用不了、无法存库/校验/分享。
- 自由节点画布（n8n 式）：连线对运营劝退；默认用竖向卡片流（分叉/循环=卡上开关滑块）。
- L3 停在运行时即兴 auto loop：产物不可见、不可改、和用户造的图两套东西。
**命门**：引擎先把原语收敛干净，产品才有复杂度可藏——①原语只写 state、路由全在边读 `next_decision`（`human_gate/curate_gate` 去掉 `Command(goto)`）；②预算闸声明式进 spec、编译器自动插闸 + 环上必有闸的编译期校验；③边条件用通用 `when:{field,op,value}`（白名单字段/算子、数据非代码，`on:label` 退化成糖）；④`PrimitiveSpec` 一处定义、编译器/L3 planner/L4 画布三处复用。**L3 不是运行时另搞一套，而是 planner 产出一张 recipe DAG → 复用 L4 的编译器跑、画布渲染成卡片流**——所以 L4 是 L3 的地基（先 L4 后 L3 才真正起作用）。
**何时回头**：现在（S5.4 内核先行、UI 最后；L3 通电 = L4 之后的 S5.5）。
**展开**：切片 S5.4.0a-e（引擎地基）/ S5.4.1a-d（编译器）/ S5.4.2a-b（配方库）/ S5.4.3a-d（卡片流画布）/ S5.5（L3 产出 DAG）。

### §6.17 会话历史 + 出错重试（复用 checkpointer）
**决策**：历史与"继续/重试"都建在 LangGraph checkpointer 上（state 已按 `group_key`=thread_id 持久）——历史=轻量 `Conversation` 索引表 + 从 checkpointer 读 `history/output`；**继续/重试同一招**=按会话 `recipe_id` 取对应图、在同一 thread 上再进图（继续=带 `Command(resume=...)`；重试=`astream(None)` 重跑挂起节点）。
**否决**：
- 另存全量消息到 `Message` 表：与 checkpointer 重复、易不一致（checkpointer 已是 single source；列表才需索引表）。
- 历史只读：未到 END 的会话（停在 human_gate / 没跑完）本就能续，只读太可惜。
- 失败整场重来：丢已完成轮次、白烧 kimi 调用；checkpointer 能断点续。
**命门**：`Conversation` 须存 `recipe_id` → 共用件 `_graph_for(recipe_id)`（None→roundtable_graph / 自定义→recompile）供继续与重试取图（历史列表按用户要求**不显示**配方，但内部留）；`resumable` = `aget_state().next` 非空；重试前端要先清掉报错那轮的半截气泡（节点从头重跑、重新流式）。LangGraph 在节点抛错处的 checkpoint = 该节点前的最后成功超步，故 `None` 续跑 = 重试该节点（实现时需 verify）。
**何时回头**：要跨会话搜索/分析消息时再建消息索引表。
**展开**：切片 S5.7（会话历史）/ S5.8（出错重试）。

### §6.18 好友双绑定解耦：身份中立 + 平台无关 + 模型无关
**决策**：好友（Contact）拆成**中立身份 + 两条可插拔绑定**，编排核心（LangGraph 图）永远只见 `contact_id` + 不透明 `group_key`，平台与模型的选择全部收敛到边界两张表。换 IM、换模型，核心零改动（延续 §6.12 transport 无关 + §6.4 LLM 归属）。
```
Contact { id,name,title,persona_style,base_stance,reputation   # 身份:平台/模型无关、可移植
          channel:{adapter,account_ref}     # 出站绑定 → 解耦平台
          llm_ref:str }                      # 推理绑定 → 解耦模型
LLMBackend { id,name,base_url,api_key_env,model,temperature,max_tokens }  # 独立注册表
ChannelDriver(adapter)  # 统一接口 send(group_key,account_ref,text)；AstrBot 桥退化成一个 driver
```
- **维度一·平台解耦（轻量 router）**：`bot_ref:str` → `channel:{adapter,account_ref}`（`adapter` 选驱动、`account_ref`=原 bot_ref）。`OutboundClient` 从 AstrBot 专用客户端升级为 **router**：按 `contact.channel.adapter` 派给对应 driver。`group_key` 重定义为 **transport 不透明令牌**（解释权归 adapter；编排层本就只透传不解析）——换平台 = 加 driver + 改 contact 的 adapter，`group_key↔平台 session` 的映射归 adapter 内部。
- **维度二·模型解耦（每好友独立 LLM）**：引入 **`ModelProvider = (contact_id) -> ChatModel`**，与 `PersonaProvider` 对称。`generate/turn` 里 `model = await model_provider(slot.contact_id)` 再 astream；模型实例**按 binding 缓存**（不每轮新建）。无绑定 → 回退全局默认 model（现状不退化）。`Contact.llm_ref → LLMBackend` 独立注册表：多好友可共享一后端（同一 key 改一处）。
- **密钥**：`LLMBackend` 只存 `api_key_env`（变量名，如 `"DEEPSEEK_KEY"`），真实 key 走环境变量——仓库可完整自包含、不泄密（延续 cmd_config 不进 git 的教训）。
**否决**：
- LLM 内联进 Contact（base_url/key/model 直存）：key 散落、同后端重复配、扩展性差。引用式 + 注册表更通用（§偏好通用可扩展）。
- key 明文落 DB：DB 文件一旦进仓库/被复制即泄密。env 引用更安全。
- 引入内部 `conversation_id` 映射层（重）：最彻底但要改 `group_key` 全链路，工作量大；当前编排层未解析 group_key，轻量重定义已够解耦，留待真接非 AstrBot 平台时再上。
**命门**：对称之美——出站靠 `channel` 解耦平台、推理靠 `llm_ref` 解耦模型、人设始终中立，好友成为换 IM/换模型都不动的"数字人格"。`ModelProvider` 必须缓存（按 backend 去重），否则每轮新建 ChatOpenAI 连接爆炸。`channel` 迁移要兼容旧 `bot_ref`（adapter 缺省 `astrbot`、account_ref=旧值）。
**何时回头**：真要接第二个 IM 平台时，落第一个非 astrbot driver；要 conversation_id 稳定映射时再上重方案。
**展开**：切片 **S7 好友双绑定解耦**（两维度一起落档、分批切片，见 §7）。

#### §6.18+ 细化（2026-06-04）：LLMBackend kind 化 + 配置可验证
学 AstrBot 的 provider 配置流程（`dashboard/routes/config.py`：`/provider/template` 类型模板 · `/check_one` 测试=`provider.test()` 打一句 `REPLY PONG ONLY` · `/model_list` 拉模型列表）。两点改进：

- **配置可验证（解决"裸填、判断不了对错"）**：不照搬 AstrBot 全类型模板（我们引擎是 ChatOpenAI／OpenAI 兼容，deepseek/kimi/openrouter/groq/ollama… 都走 `/v1`，一个 openai 兼容类型≈全覆盖）。只加性价比最高的两件：① **测试端点**（解析 `api_key_env` 真 key → 真打一次 ping → 绿/红立判，仿 check_one）；② **拉模型列表**（`GET {base_url}/v1/models` 能拉就下拉选、拉不到回退手填，仿 model_list）。base_url 预设/全类型模板暂不做。
- **LLMBackend kind 化（"AstrBot 当后端"）**：让 llm 绑定与 channel **完全对称**——都是 kind/adapter 化的可插拔绑定。
  ```
  LLMBackend { id,name, kind,                       # kind ∈ {openai, astrbot, ...}
               base_url,api_key_env,model,temperature,max_tokens,   # kind=openai 用
               provider_id }                          # kind=astrbot 用
  ```
  - `kind=openai`（现状默认）：`make_chat_model_from_backend` 造 ChatOpenAI。**自包含、不依赖 AstrBot**——S6「pip 单机不碰 telegram 也能用」的命根，必须保留。
  - `kind=astrbot`：后端只存 AstrBot `provider_id`；`ModelProvider` 返回薄适配器，调桥 `POST /llm {provider_id, messages}` → 插件 `Context.get_provider_by_id(id).text_chat(...)`（已确认 `Context` 暴露 `get_provider_by_id`/`provider_manager.inst_map`）。**复用 AstrBot 已配好、已测过的 provider**（key/模型/校验都在那边）。"这个好友用 AstrBot 的配置" = 它的后端 `kind=astrbot`。

  **统一**：channel.adapter ∈ {astrbot,…}、llm.kind ∈ {openai,astrbot,…}——两维度同构。
  **取舍（记）**：`kind=astrbot` 走 HTTP 委托时**流式**是麻烦点——引擎吃 `astream`，而 AstrBot `text_chat` 非流式（`text_chat_stream` 有但要把 SSE 也桥过去）。MVP 先**非流式**（那一轮等全文再吐），流式后补。
**展开**：切片 **S7.1d**（配置可验证：测试 + 拉模型列表）/ **S7.1e**（LLMBackend kind 化 + astrbot 委托），见 §7。

---

## 7. MVP 落地顺序（每步独立可验）

对应需求 §6 步骤 1-4，按"配方抽象"重排，**前端继承(§12)随产品域并入**。每片完成当场验证。

| 切片 | 引擎 / 服务 | 前端（继承改造，§12） | 完成判据 |
|---|---|---|---|
| **S1 扇出配方+策展UI** | 编排服务骨架 + 原语节点(CLARIFY/FRAME/FANOUT/CURATE/SYNTHESIZE) + 组合机制；落「扇出策展」配方；**mock 适配器** | 前端基座(fork dashboard、剥死代码、双后端 axios、外壳直用) + **CuratePage 最小版**连 brain | 在 CuratePage 提需求 → N agent 并行出 N 候选 → 人能 pick/eliminate/reassign → 结果正确 |
| **S2 持久+身份+信誉** | Contact/Group/Message + 人设注入 + checkpointer + 信誉软加权字段 | **`PersonaPage` 复用成 Contact 注册表** | 重启后身份/历史在；同一好友两场不同维度；CuratePage 能从库选好友入场 |
| **S3 圆桌配方+群视图** | 用**同组原语零改引擎**拼「圆桌」配方(TURN⇄SCHEDULE) + 人在环打断 | **`ChatPage` 改造成群视图**(多身份气泡+人插话) + RecipePicker(L1 选配方) | 圆桌配方跑通且 git diff **未动引擎代码**(§6.6)；群视图里人插话能接住改方向 |
| **S4 AstrBot 桥+多bot** | `group_relay` 插件(去重+stop_event / send_by_session) | `PlatformPage` 复用成 N bot 管理(连 AstrBot 后端) | Telegram 群发问，N bot **各以独立身份**发言，人能插话/策展 |

> 每片的"引擎"与"前端"列在执行时可各自再细分为更小的一刀（A1 切片纪律），表里合并只为展示对应关系。S1-S3 在本地/web 跑通整套逻辑；**S3 是抽象的验收点**(加模式不改引擎)；S4 才碰真实多 bot/Telegram。

---

## 8. 风险与待定

1. **AstrBot 群消息钩子与 `MessageSession` 还原**：S4 前需验证插件能拿到 `unified_msg_origin` 并据此 `send_by_session` 回同一群（本方案按 `platform.py` 接口推断可行，未跑通）。
2. **Telegram privacy mode**：bot 要"听到"全群消息须关闭 privacy mode（需求 §5.3 已记）。
3. **去重窗口**：`(group_key, native_msg_id)` 缓存的 TTL/容量，避免内存泄漏。
4. **QQ 官方配额**：生产迁移头号风险，本 MVP 不碰（先 Telegram）。
5. **编排↔AstrBot 桥形态**：MVP 用 HTTP 够；高频可换 WS/队列，属实现细节。

---

## 9. 与需求文档的对应关系

| 需求文档 | 本方案落点 |
|---|---|
| §5.3 N bot 推送 | §1 架构 + §2.2 出站 `send_by_session` |
| §5.4 混合身份 | §4 + §5 Contact 模型 |
| §5.5 调度循环(心脏) | §3 LangGraph 状态机 |
| §5.6 适配器解耦 | §6.1 集成模型决策 |
| §6 步骤 1-4 | §7 切片 S1-S4 |
| §12.4 调度器换框架(倾向D) | 编排服务独立 → 换框架只动它 |
| 交互模式 / 编辑场景 | §10 原语 + 配方库；§6.6 决策 |
| Web UI / 人在环(§6 步骤3) | §12 继承 dashboard；§6.8 决策 |
| §7/§8/§9 | **本方案不展开**，留待后续 Phase |

---

## 10. 协作模式 = 可组合的配方（核心抽象）

> 起因：质疑"每个 AI 依次发言太慢"，带来下面的真实场景。结论：这不是快慢问题——圆桌(讨论)与扇出(生产)是两种协作模式，而**模式不该写死进引擎**。引擎只提供**原语**，模式 = 用原语拼出的**配方(recipe)**；生产者维护配方库，不同用户的工作方式 = 选/拼不同配方。踩在已选的 LangGraph 上：图=拓扑，换模式=换图，不动内核。

### 10.1 真实场景（编辑的并行策展工作流）

一位编辑的真实用法：

```
同一个需求 → 并行开 6 个 AI，各自独立作答（互不可见）
  → 编辑读全部 6 份
  → 从每份里挑出"可行的点"
  → 淘汰几个做得差的 AI
  → 把 A 提的可行点，交给 B 去写（因为 B 的写作更合她的要求）
  → 产出
```

**关键洞察**：
1. **并行，不依次**：6 个 AI 同时生成，编辑要的是 N 个候选，不是一场讨论。
2. **人当策展人/编辑**：选点、淘汰、定稿都由真人做。
3. **想法来源 ≠ 执行质量**：A 出好点子但 B 写得好 → 把 A 的点交给 B 执行。这是"内容生产"区别于"圆桌辩论"的核心。
4. **人工淘汰**：由真人驱动，**不是** §8 否决的"AI 裁判自动淘汰"——真人取舍正是 §8.6/§9.5 苦求的"硬客观反馈源"。

### 10.2 协作原语集（引擎只提供这些积木，跑在共享 `GroupState` 上）

```
CLARIFY     主持人信心触发的需求澄清                       (§6.5)
FRAME       主持人分维度 / 派角色
FANOUT(n)   并行生成 n 份, 各 agent 互不可见               ← 扇出
TURN        一个 agent 发言, 能看到上文                     ← 圆桌
SCHEDULE    决策: 下一个谁说 / 让位 / 停                     ← 圆桌
CURATE      人工: pick(挑点) / eliminate(淘汰) / reassign(跨搬运)  ← 扇出
SYNTHESIZE  收敛成产出
INTERRUPT   人随时插话注入 (横切, 永远可用)
```
组合算子：顺序(A→B) · 循环(SCHEDULE ⇄ TURN) · 条件分支。`reassign` = "A 的点交 B 写"，把想法来源与执行质量解耦。

### 10.3 模式 = 配方（同组原语的不同接线）

```
圆桌(讨论) = CLARIFY → FRAME → (TURN ⇄ SCHEDULE)* → SYNTHESIZE
扇出(策展) = CLARIFY → FRAME → FANOUT(n) → CURATE →〔reassign → TURN〕* → SYNTHESIZE
评审/红队… = 另一种接线（将来加，不改引擎）
```

加一个工作模式 = **写一张配方图**，不是改代码加 if/else。

> **原语两类（§6.10，模型 A）**：自包含原语（CLARIFY/FRAME/FANOUT/TURN/SCHEDULE/SYNTHESIZE）是 `state→delta` 的图节点；人在环原语（CURATE）需图外人的输入，用 `interrupt`（暂停—等人—resume，多轮循环）进图。所以配方里的 CURATE 实为一个 interrupt 节点，整张图仍是纯组合，无 service 层特例。

两张配方的性质差异：

| | **圆桌配方** | **扇出配方** |
|---|---|---|
| AI 之间 | 互相可见、交锋收敛 | 互不可见、各写各的 |
| 调度 | 主持人逐个点名（串行 O(N)） | 一次性并行（O(1)，≈最慢那个） |
| 人的角色 | 平等参与者，随时插话 | 策展人/编辑，事后挑选 |
| 适合 | 讨论一个问题、碰撞出结论 | 要 N 份候选稿、人工定稿 |

> "依次发言太慢"在配方层自然化解：要快/要候选就用扇出(并行)，要碰撞就用圆桌——同一引擎，按任务选配方。

### 10.4 弹性三层：MVP 行为 L1 + 设计预留 L3（决策见 §6.6）

```
L1 选配方   用户从库里挑               ← MVP 行为做到这层
L2 荐配方   主持人按任务自动建议         ← 后续
L3 自拼     power user 用原语组自己的配方  ← MVP 不做行为, 但原语/GroupState 为它留缝
```

MVP 行为只做 L1；但原语边界正交、`GroupState` 字段可扩展，从第一天就按"配方可外部组合"设计，避免开放 L2/L3 时回头重构。

### 10.5 与 §8/§9 原则的呼应（顺带解了两个老结）

- **人工淘汰是合法的**：§8 反对的是"AI 互评自动淘汰"（收敛到裁判口味）。这里淘汰者是真人编辑 = §8.6 说的硬反馈源，落为 §8.4"信誉软加权"（淘汰只影响本场/下次邀请，可逆，非处决）。
- **跨搬运 = 显式信用分配**：§8.2 的"信用分配无解"在扇出模式里被真人剪开——"点子算 A、执行算 B"由编辑当场判定，不需 AI 互评。

---

## 11. Phase 2 前瞻：复用 AstrBot 的执行层（skill / MCP / 沙盒）

> 对应需求 §9 执行层，**出当前 MVP 范围**，本节只作前瞻记录，不改 §7 切片。决策见 §6.7。

### 11.1 AstrBot 实际提供了什么（实读）

| 能力 | 实现 | 关键事实 |
|---|---|---|
| **沙盒** | `core/computer/`：`ComputerBooter` 抽象 + 多后端(`local`/`boxlite`/`cua`/`shipyard`/`shipyard_neo`) | 组件化(fs/python/shell/browser/gui)、按 session 启停、含**远程执行后端**；隔离+安全已做好 |
| **skill** | `core/skills/skill_manager.py` | = Claude-Code 式 `SKILL.md`+脚本资源，挂进沙盒 `/workspace/skills/<name>/`，**依附沙盒** |
| **MCP** | `agent/mcp_client.py` | 标准 MCP 客户端(stdio/SSE/HTTP) + 命令白名单安全 |

### 11.2 按复用经济学分三种处理

- **沙盒 = 复用（最值）**：§9 里最难的工程（隔离/安全/多后端含远程），重写不划算。
- **MCP = 大脑自持**：标准协议，工具*决策*属推理，绕 AstrBot 是白加一跳。**例外**：需跑在沙盒内的 MCP server 随沙盒走。
- **skill = 搭沙盒便车**：复用沙盒则 `SKILL.md` 同步白送；"用哪个 skill"的决策留大脑（= §10 recipe/SOP）。

### 11.3 接法：沿用 §2 的桥，零抽取

不拆沙盒；在 AstrBot 侧 `group_relay` 插件加一个执行端点，进程内复用 `computer_client`/`skill_manager`（`Context` 现成）：

```
大脑(LangGraph) ReAct 一步"跑代码/跑工具"
   │  POST AstrBot /exec
   ▼  ExecReq{session_id, kind: python|shell|sandbox_mcp, payload}
AstrBot 插件 → ComputerBooter.boot(session_id).python/shell.run(...)
   ▲  ExecObs{stdout, stderr, files, exit_code}
   └─ 这份客观观察 = 需求 §9.5 的"硬奖励信号"(代码跑没跑通)
```

→ **AstrBot 升级为"脸(bot 适配)+手(沙盒执行)"**，大脑仍独占推理/调度，不违背 §6.1。

### 11.4 风险

- **耦合未实测**：`computer_client` 依赖 `star.context.Context`，"插件内暴露 `/exec`"按 import 关系推断可行，未跑通。
- **后端选型继承**：复用 AstrBot 沙盒 = 把 `local`/`boxlite`/远程 `shipyard`（对应需求 §9.9 子进程/容器/远程）的选型一并继承，可配。

---

## 12. 前端：继承并改造 AstrBot Dashboard（纳入 MVP）

> AstrBot dashboard 是 Vue3 + Vuetify 的 SPA（CodedThemes 模板底子）。继承的关键是**按它连哪个后端切分**，不是全要。决策见 §6.8；产品域并入 §7 切片。

### 12.1 按后端切三层

| 层 | 包含 | 处理 | 连谁 |
|---|---|---|---|
| **共享外壳**（白嫖，最大价值） | layout/router、JWT 鉴权、theme/i18n、markdown+mermaid+katex 渲染、**流式气泡**(markstream/stream-markdown)、monaco | 直接用 | — |
| **管理域** | `PlatformPage` `PersonaPage` `ProviderPage` `ConsolePage` `Settings` `stats` | 原样/小改复用 | AstrBot 后端(:6185) |
| **产品域** | `ChatPage` `ChatBoxPage` `ConversationPage` | **改造重点**，转向连大脑 | LangGraph 服务 |

### 12.2 改造清单（按工作量）

- **白嫖**：整个外壳——鉴权/主题/i18n/流式渲染现成，这是继承的真金。
- **复用**：`PersonaPage` → 好友(Contact)注册表（人设字段对得上 §5 Contact）；`PlatformPage` → N 个 bot 管理。
- **新建**：
  - 群视图（`ChatPage` 基础上加多 AI 身份气泡 + 人插话）——圆桌配方。
  - **`CuratePage` 策展视图**：N 候选并排 + `pick`/`eliminate`/`reassign`——扇出配方，**AstrBot 无对应，纯新建的重头**。
  - `RecipePicker`：L1 选配方入口。

### 12.3 双后端与风险

- **双后端**：前端同时连 AstrBot(管理域) + 大脑(产品域)。axios 两 client(`adminApi`/`brainApi`)，鉴权打通。
- **剥离死代码**：dashboard 强耦合 AstrBot 自身 stores/api，fork 后剪掉用不上的管理页（知识库/cron/插件市场…），别拖商业模板体积与死代码。
