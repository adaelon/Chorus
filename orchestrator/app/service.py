"""扇出配方的 HTTP 服务 + Contact 注册表 CRUD。

端点：
  GET  /health
  POST /inbound    {group_key, request, roster:[contact_id]} → 跑生成段 → {candidates}
  POST /curate     {group_key, commands:[...]}               → apply 策展 → {candidates, picked}
  POST /synthesize {group_key}                               → 汇成产出 → {output}
  GET/POST/PUT/DELETE /contacts                              → Contact 注册表 CRUD（S2.4）

S2.0：checkpointer 默认 AsyncSqliteSaver；S2.4：lifespan 起注册表 DB，
把 persona_provider / reputation_adjuster 接进 live（真实人设进 prompt、信誉落库）。
测试可注入 MemorySaver / 假 generate / 临时 registry_db_path，保持离线。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field
from sqlmodel import select

from .db.engine import init_models, make_engine, make_session_factory
from .db.models import Contact
from .db.repo import persona_provider_from, reputation_adjuster_from
from .nodes.curate import CurateCommand, Eliminate, Pick, Reassign, ReputationAdjuster, curate
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.synthesize import synthesize
from .recipes import build_fanout_recipe
from .state import AgentSlot, GroupState, Msg

AnyCommand = Annotated[Union[Pick, Eliminate, Reassign], Field(discriminator="kind")]


class InboundReq(BaseModel):
    group_key: str
    request: str
    roster: list[str]  # contact_ids（来自 Contact 注册表）


class CurateReq(BaseModel):
    group_key: str
    commands: list[AnyCommand]


class GroupReq(BaseModel):
    group_key: str


class ContactIn(BaseModel):
    id: str
    name: str
    title: str = ""
    persona_style: str = ""
    base_stance: str = ""


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
    reputation_adjuster: ReputationAdjuster | None = None,
    db_path: str = "group_checkpoints.sqlite",
    registry_db_path: str = "chorus_registry.sqlite",
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 注册表 DB（Contact 等）；persona/信誉默认由它支持，可被显式注入覆盖。
        registry_engine = make_engine(registry_db_path)
        await init_models(registry_engine)
        sf = make_session_factory(registry_engine)
        app.state.session_factory = sf
        pp = persona_provider or persona_provider_from(sf)
        ra = reputation_adjuster or reputation_adjuster_from(sf)
        app.state.persona_provider = pp
        app.state.reputation_adjuster = ra
        try:
            if checkpointer is not None:
                app.state.graph = build_fanout_recipe(
                    checkpointer, assign=assign, generate=generate, persona_provider=pp
                )
                yield
            else:
                async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
                    app.state.graph = build_fanout_recipe(
                        saver, assign=assign, generate=generate, persona_provider=pp
                    )
                    yield
        finally:
            await registry_engine.dispose()

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
            state,
            commands,
            generate=generate,
            persona_provider=request.app.state.persona_provider,
            reputation_adjuster=request.app.state.reputation_adjuster,
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

    # ---- Contact 注册表 CRUD（S2.4）----

    @app.get("/contacts")
    async def list_contacts(request: Request):
        async with request.app.state.session_factory() as s:
            return (await s.exec(select(Contact))).all()

    @app.post("/contacts")
    async def create_contact(c: ContactIn, request: Request):
        async with request.app.state.session_factory() as s:
            if await s.get(Contact, c.id) is not None:
                raise HTTPException(status_code=409, detail=f"contact {c.id} exists")
            obj = Contact(**c.model_dump())
            s.add(obj)
            await s.commit()
            return obj

    @app.put("/contacts/{cid}")
    async def update_contact(cid: str, c: ContactIn, request: Request):
        async with request.app.state.session_factory() as s:
            obj = await s.get(Contact, cid)
            if obj is None:
                raise HTTPException(status_code=404, detail=f"contact {cid} not found")
            for k, v in c.model_dump(exclude={"id"}).items():
                setattr(obj, k, v)
            s.add(obj)
            await s.commit()
            return obj

    @app.delete("/contacts/{cid}")
    async def delete_contact(cid: str, request: Request):
        async with request.app.state.session_factory() as s:
            obj = await s.get(Contact, cid)
            if obj is None:
                raise HTTPException(status_code=404, detail=f"contact {cid} not found")
            await s.delete(obj)
            await s.commit()
            return {"deleted": cid}

    return app
