"""扇出配方的 HTTP 服务。

端点：
  GET  /health
  POST /inbound    {group_key, request, roster:[contact_id]} → 跑生成段 → {candidates}
  POST /curate     {group_key, commands:[...]}               → apply 策展 → {candidates, picked}
  POST /synthesize {group_key}                               → 汇成产出 → {output}

S2.0：默认用 **AsyncSqliteSaver**（跨重启持久），经 FastAPI lifespan 管理异步
连接、启动时建图挂 `app.state.graph`。测试可注入 `MemorySaver`（快、无文件）。
节点 LLM 依赖（assign/generate）可注入，便于离线 e2e。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from .nodes.curate import CurateCommand, Eliminate, Pick, Reassign, curate
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.synthesize import synthesize
from .recipes import build_fanout_recipe
from .state import AgentSlot, GroupState, Msg

AnyCommand = Annotated[Union[Pick, Eliminate, Reassign], Field(discriminator="kind")]


class InboundReq(BaseModel):
    group_key: str
    request: str
    roster: list[str]  # contact_ids（Contact 注册表见 S2.1）


class CurateReq(BaseModel):
    group_key: str
    commands: list[AnyCommand]


class GroupReq(BaseModel):
    group_key: str


def _cfg(group_key: str) -> dict:
    return {"configurable": {"thread_id": group_key}}


async def _load_state(graph, group_key: str) -> GroupState:
    snap = await graph.aget_state(_cfg(group_key))
    if not snap.values:
        raise HTTPException(
            status_code=404,
            detail=f"group {group_key} not found; call /inbound first",
        )
    return GroupState(**snap.values)


def create_app(
    *,
    checkpointer=None,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    db_path: str = "group_checkpoints.sqlite",
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if checkpointer is not None:
            # 注入式（测试用 MemorySaver，或显式给定 saver）
            app.state.graph = build_fanout_recipe(
                checkpointer, assign=assign, generate=generate, persona_provider=persona_provider
            )
            yield
        else:
            # 默认：durable，跨重启持久
            async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
                app.state.graph = build_fanout_recipe(
                    saver, assign=assign, generate=generate, persona_provider=persona_provider
                )
                yield

    app = FastAPI(title="Chorus orchestrator (fanout)", lifespan=lifespan)
    # 开发期允许前端(vite :5173)跨域访问 brainApi；生产应收紧 allow_origins。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/inbound")
    async def inbound(req: InboundReq, request: Request):
        graph = request.app.state.graph
        state_in = {
            "group_key": req.group_key,
            "roster": [AgentSlot(contact_id=c) for c in req.roster],
            "pending_human": Msg(
                sender_id="human", sender_kind="human", text=req.request
            ),
        }
        out = await graph.ainvoke(state_in, _cfg(req.group_key))
        return {"candidates": out["candidates"]}

    @app.post("/curate")
    async def curate_ep(req: CurateReq, request: Request):
        graph = request.app.state.graph
        state = await _load_state(graph, req.group_key)
        commands: list[CurateCommand] = list(req.commands)
        result = await curate(
            state, commands, generate=generate, persona_provider=persona_provider
        )
        await graph.aupdate_state(_cfg(req.group_key), result)
        return result

    @app.post("/synthesize")
    async def synthesize_ep(req: GroupReq, request: Request):
        graph = request.app.state.graph
        state = await _load_state(graph, req.group_key)
        result = await synthesize(state)
        await graph.aupdate_state(_cfg(req.group_key), result)
        return result

    return app
