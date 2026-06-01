"""S1.6: 扇出配方的 HTTP 服务。

端点：
  POST /inbound    {group_key, request, roster:[contact_id]} → 跑生成段 → {candidates}
  POST /curate     {group_key, commands:[...]}               → apply 策展 → {candidates, picked}
  POST /synthesize {group_key}                               → 汇成产出 → {output}

S1.6 用 MemorySaver（会话内持久；跨重启的 durable checkpointer 留后）；
节点 LLM 依赖可注入（assign/generate），便于离线 e2e。
"""

from __future__ import annotations

from typing import Annotated, Union

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from .nodes.curate import CurateCommand, Eliminate, Pick, Reassign, curate
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn
from .nodes.synthesize import synthesize
from .recipes import build_fanout_recipe
from .state import AgentSlot, GroupState, Msg

AnyCommand = Annotated[Union[Pick, Eliminate, Reassign], Field(discriminator="kind")]


class InboundReq(BaseModel):
    group_key: str
    request: str
    roster: list[str]  # contact_ids（Contact 注册表见 S2）


class CurateReq(BaseModel):
    group_key: str
    commands: list[AnyCommand]


class GroupReq(BaseModel):
    group_key: str


def create_app(
    *,
    checkpointer=None,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
) -> FastAPI:
    cp = checkpointer or MemorySaver()
    graph = build_fanout_recipe(cp, assign=assign, generate=generate)
    app = FastAPI(title="Chorus orchestrator (fanout)")

    def cfg(group_key: str) -> dict:
        return {"configurable": {"thread_id": group_key}}

    async def load_state(group_key: str) -> GroupState:
        snap = await graph.aget_state(cfg(group_key))
        if not snap.values:
            raise HTTPException(
                status_code=404,
                detail=f"group {group_key} not found; call /inbound first",
            )
        return GroupState(**snap.values)

    @app.post("/inbound")
    async def inbound(req: InboundReq):
        state_in = {
            "group_key": req.group_key,
            "roster": [AgentSlot(contact_id=c) for c in req.roster],
            "pending_human": Msg(
                sender_id="human", sender_kind="human", text=req.request
            ),
        }
        out = await graph.ainvoke(state_in, cfg(req.group_key))
        return {"candidates": out["candidates"]}

    @app.post("/curate")
    async def curate_ep(req: CurateReq):
        state = await load_state(req.group_key)
        commands: list[CurateCommand] = list(req.commands)
        result = await curate(state, commands, generate=generate)
        await graph.aupdate_state(cfg(req.group_key), result)
        return result

    @app.post("/synthesize")
    async def synthesize_ep(req: GroupReq):
        state = await load_state(req.group_key)
        result = await synthesize(state)
        await graph.aupdate_state(cfg(req.group_key), result)
        return result

    return app
