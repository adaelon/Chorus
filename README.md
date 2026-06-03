# Chorus

对话式多 agent 编排——「AI 微信群」。多个有**持久身份**的 AI 好友在群里**圆桌讨论**或**并行产出**，你作为编辑 / CEO **在环掌舵**（挑选、追问、改方向）。

不止是固定的几种群聊模式：协作流程本身是**可组合、可编辑、可由 AI 现搭**的——内核是一套「原语 + 声明式有向图（配方）」。

## 核心理念

**引擎只提供原语，模式 = 原语拼成的有向图（配方）**。圆桌 / 扇出策展 / 自动主持都是同一套原语的不同接线。在此之上，协作的"谁来定流程"分四层：

| 层 | 谁定流程 | 形态 |
|---|---|---|
| **L1** 用户选 | 用户 | 首页挑一张现成配方 |
| **L2** 主持人荐 | LLM | 说任务 → 自动选 圆桌/扇出 |
| **L3** AI 搭 | LLM | 说任务 → AI 现搭一张配方（落库、可看可改可跑） |
| **L4** 用户自拼 | 用户 | 卡片流画布上增删卡 / 改连线 / 实时校验 |

四层都落到同一条管线：**声明式 DAG（JSON）→ 编译器 → 配方库 → 卡片流画布 / 运行**。原语契约与能力边界见 [`docs/引擎能力与原语.md`](docs/引擎能力与原语.md)。

## 架构

- **LangGraph 编排服务**（大脑，`orchestrator/`）：原语节点、配方编译/校验、调度循环、混合身份、点账本记忆、持久化、SSE 流式。
- **Vue3 + Vuetify 前端**（`web/`）：圆桌群视图 / 扇出策展 / 好友注册表 / **配方库与卡片流画布**。
- **AstrBot `group_relay` 插件**（脸，`astrbot/data/plugins/group_relay/`，可选）：N 个独立 telegram bot 收发消息，与大脑消息进出解耦（§6.15：进不了 pip，外置分发）。

详见 [`docs/技术方案-基于AstrBot的MVP实现.md`](docs/技术方案-基于AstrBot的MVP实现.md)。

## 目录

- `orchestrator/` — LangGraph 编排服务（`app/` 下分 `nodes/` 原语、`recipes/` 配方子系统、`transport/` 传输层、`db/` 持久层）。
- `web/` — 前端 SPA。
- `astrbot/` — vendored AstrBot；仅 `group_relay` 插件入库（telegram 桥）。
- `docs/` — 技术方案 / 切片计划 / 代码链路 / **引擎能力与原语**。

## 运行

**后端（大脑，:8900）**
```bash
cd orchestrator
python -m venv .venv
.venv/Scripts/python -m pip install -e .            # 或装 langgraph / langgraph-checkpoint-sqlite / fastapi / uvicorn / sqlmodel / langchain-openai / pydantic
.venv/Scripts/python -m pytest                       # 跑切片判据（当前 157 passed）
.venv/Scripts/python -m app.server                   # 起完整服务（真实 LLM，需配 LLM 环境）
```
LLM 配置经环境变量 / `orchestrator/.env`（见 `app/config.py`）。

**前端（:5173）**
```bash
cd web
npm install
npm run dev          # 浏览器开 http://localhost:5173（连后端 :8900）
```

**telegram 桥（可选）**：把 `astrbot/data/plugins/group_relay/` 装进 AstrBot，配 bot token 与 `brain_url`（见该目录 README）。

## 进度

S1–S5 已完成：扇出策展、持久身份 + 信誉、圆桌配方 + 群视图、AstrBot 多 bot 桥、transport 无关运行时、配方分层 L1→L4（原语三态化 + 声明式 DAG 编译/校验/带闸 + 配方库 + 卡片流画布 + AI 现搭配方）。规划中：S5.6 分层圆桌、S6 pip 发布。

## License

[AGPL-3.0](LICENSE)（与上游 AstrBot 一致）。
