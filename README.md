# Chorus

对话式多 agent 编排——「AI 微信群」。多个有**持久身份**的 AI 好友在群里圆桌讨论或并行产出，你作为编辑 / CEO **在环掌舵**（挑选、追问、改方向）。

## 架构

- **AstrBot 瘦适配层**（脸）：N 个独立 bot 收发消息。
- **LangGraph 编排服务**（大脑）：调度循环、配方(recipe)、混合身份、持久化。
- 二者消息进出解耦。详见 [`docs/技术方案-基于AstrBot的MVP实现.md`](docs/技术方案-基于AstrBot的MVP实现.md)。

## 目录

- `orchestrator/` — LangGraph 编排服务（Python 3.12+）。
- `docs/` — 设计文档：技术方案 / 切片计划 / 代码链路 / 需求方案。

## 开发

```bash
cd orchestrator
python -m venv .venv
.venv/Scripts/python -m pip install langgraph langgraph-checkpoint-sqlite "pydantic>=2" pytest
.venv/Scripts/python -m pytest      # 跑切片判据
.venv/Scripts/python -m app         # 起最小服务骨架
```

## License

[AGPL-3.0](LICENSE)（与上游 AstrBot 一致）。
