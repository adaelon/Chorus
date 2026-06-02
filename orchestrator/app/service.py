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

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlmodel import select

from .db.engine import init_models, make_engine, make_session_factory
from .db.models import Contact
from .db.repo import persona_provider_from, reputation_adjuster_from
from .nodes.clarify import ClarifyFn
from .nodes.curate import Eliminate, Pick, Reassign, ReputationAdjuster
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn, PersonaProvider
from .recipes import build_fanout_recipe
from .state import AgentSlot, Msg

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


class ClarifyReq(BaseModel):
    group_key: str
    answer: str | None = None  # 答复澄清问（并入 history 后进 FRAME）
    skip: bool = False  # 跳过澄清，强制进 FRAME


class ContactIn(BaseModel):
    id: str
    name: str
    title: str = ""
    persona_style: str = ""
    base_stance: str = ""


def _cfg(group_key: str) -> dict:
    return {"configurable": {"thread_id": group_key}}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _to_event(mode: str, payload) -> dict | None:
    """把 LangGraph astream 的 (mode,payload) 转成 SSE 事件；不关心的返回 None。"""
    if mode == "messages":
        chunk, meta = payload
        content = getattr(chunk, "content", "")
        if not content:  # 跳过空 content（reasoning 阶段）
            return None
        tags = meta.get("tags") or []
        cid = next((t.split(":", 1)[1] for t in tags if t.startswith("agent:")), None)
        if meta.get("langgraph_node") == "fanout" and cid:
            text = content if isinstance(content, str) else str(content)
            return {"type": "delta", "contact_id": cid, "text": text}
        return None
    if mode == "updates":
        for node, delta in (payload or {}).items():
            if not delta:
                continue
            if node == "frame" and "roster" in delta:
                return {
                    "type": "framed",
                    "roster": [
                        {"contact_id": s.contact_id, "dimension": s.dimension}
                        for s in delta["roster"]
                    ],
                }
            if node == "fanout" and "candidates" in delta:
                return {
                    "type": "candidates",
                    "candidates": [c.model_dump() for c in delta["candidates"]],
                }
    return None


def _interrupt_value(result) -> dict | None:
    """从 ainvoke 结果取 interrupt payload（图暂停在 curate/clarify）；未暂停返回 None。"""
    intr = result.get("__interrupt__") if isinstance(result, dict) else None
    if intr:
        return intr[0].value
    return None


def _inbound_result(result) -> dict:
    """按 interrupt payload 的 type 分流扇出响应：clarify（待答澄清）vs candidates（候选）。

    clarify live wire 后，图可能先停在 clarify interrupt（信心不足）；前端据 type 渲染
    澄清问气泡或候选卡。answer/skip 经 /clarify resume 后图续跑到 curate → 走 candidates 支。
    """
    payload = _interrupt_value(result)
    if payload and payload.get("type") == "clarify":
        return {
            "type": "clarify",
            "restate": payload.get("restate", ""),
            "question": payload.get("question", ""),
        }
    candidates = payload["candidates"] if payload else result.get("candidates", [])
    return {"type": "candidates", "candidates": candidates}


async def _require_thread(graph, group_key: str) -> None:
    """resume 前置校验：线程须已有 checkpoint（即先 /inbound 过）。"""
    snap = await graph.aget_state(_cfg(group_key))
    if not snap.values:
        raise HTTPException(
            status_code=404,
            detail=f"group {group_key} not found; call /inbound first",
        )


def create_app(
    *,
    checkpointer=None,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
    clarify_assess: ClarifyFn | None = None,
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
                    checkpointer,
                    assign=assign,
                    generate=generate,
                    persona_provider=pp,
                    reputation_adjuster=ra,
                    clarify_assess=clarify_assess,
                )
                yield
            else:
                async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
                    app.state.graph = build_fanout_recipe(
                        saver,
                        assign=assign,
                        generate=generate,
                        persona_provider=pp,
                        reputation_adjuster=ra,
                        clarify_assess=clarify_assess,
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
        # 跑到 CLARIFY（信心不足）或 CURATE 的 interrupt 暂停；按 type 分流响应。
        result = await graph.ainvoke(state_in, _cfg(req.group_key))
        return _inbound_result(result)

    @app.post("/clarify")
    async def clarify_ep(req: ClarifyReq, request: Request):
        """resume 停在 clarify interrupt 的图：答复并入 history / 跳过，均续跑到 curate。

        resume 必须非空（LangGraph 把 falsy resume 当未恢复会重触发 interrupt）：
        skip→{"skip": True}；否则→{"answer": text}。
        """
        graph = request.app.state.graph
        cfg = _cfg(req.group_key)
        await _require_thread(graph, req.group_key)
        resume = {"skip": True} if req.skip else {"answer": req.answer or ""}
        result = await graph.ainvoke(Command(resume=resume), cfg)
        return _inbound_result(result)

    @app.post("/curate")
    async def curate_ep(req: CurateReq, request: Request):
        graph = request.app.state.graph
        cfg = _cfg(req.group_key)
        await _require_thread(graph, req.group_key)
        # resume：apply 指令 → 图回到 CURATE 再次 interrupt，新候选/picked 从 payload 取。
        result = await graph.ainvoke(
            Command(
                resume={
                    "action": "curate",
                    "commands": [c.model_dump() for c in req.commands],
                }
            ),
            cfg,
        )
        payload = _interrupt_value(result)
        if payload:
            return {"candidates": payload["candidates"], "picked": payload["picked"]}
        return {
            "candidates": result.get("candidates", []),
            "picked": result.get("picked", []),
        }

    @app.post("/inbound/stream")
    async def inbound_stream(req: InboundReq, request: Request):
        """SSE 流式版 /inbound：边生成边推 token（按 agent 路由），心跳保活。

        事件：framed(维度) / delta(某 agent 的 token) / candidates(最终) / done / error。
        """
        graph = request.app.state.graph
        state_in = {
            "group_key": req.group_key,
            "roster": [AgentSlot(contact_id=c) for c in req.roster],
            "pending_human": Msg(sender_id="human", sender_kind="human", text=req.request),
        }
        cfg = _cfg(req.group_key)
        queue: asyncio.Queue = asyncio.Queue()

        async def produce():
            try:
                await queue.put({"type": "status", "stage": "framing"})
                async for mode, payload in graph.astream(
                    state_in, cfg, stream_mode=["updates", "messages"]
                ):
                    ev = _to_event(mode, payload)
                    if ev:
                        await queue.put(ev)
            except Exception as e:  # noqa: BLE001
                await queue.put({"type": "error", "detail": str(e)})
            finally:
                await queue.put({"type": "done"})

        async def gen():
            task = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"  # 保活：frame 思考/长沉默时防断连
                        continue
                    yield _sse(ev)
                    if ev.get("type") == "done":
                        break
            finally:
                task.cancel()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/synthesize")
    async def synthesize_ep(req: GroupReq, request: Request):
        graph = request.app.state.graph
        cfg = _cfg(req.group_key)
        await _require_thread(graph, req.group_key)
        # resume(synthesize)：CURATE 让位 → SYNTHESIZE 终端节点跑到 END，读最终 output。
        result = await graph.ainvoke(Command(resume={"action": "synthesize"}), cfg)
        return {"output": result.get("output")}

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
