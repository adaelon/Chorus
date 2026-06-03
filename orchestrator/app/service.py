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
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlmodel import select

from .db.engine import init_models, make_engine, make_session_factory
from .db.models import Contact, Conversation, Recipe
from .db.repo import (
    bot_ref_provider_from,
    persona_provider_from,
    reputation_adjuster_from,
    roster_provider_from,
    seed_builtin_recipes,
    upsert_conversation,
)
from .nodes.clarify import ClarifyFn
from .nodes.curate import Eliminate, Pick, Reassign, ReputationAdjuster
from .nodes.extract import ClaimExtractor
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.plan import PlanFn
from .nodes.schedule import PickFn
from .nodes.synthesize import ComposeFn
from .recipes import (
    REGISTRY,
    RecipePlanner,
    RecipeSelector,
    build_fanout_recipe,
    build_roundtable_recipe,
    compile_recipe,
    plan_recipe,
    select_recipe,
    validate_recipe,
)
from .state import AgentSlot, Msg
from .transport import OutboundClient, RelayDriver, iter_events

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


class RecipeSelectReq(BaseModel):
    task: str  # 用户任务/需求；主持人据此荐配方（L2，§6.13）


class ClarifyReq(BaseModel):
    group_key: str
    answer: str | None = None  # 答复澄清问（并入 history 后进 FRAME）
    skip: bool = False  # 跳过澄清，强制进 FRAME


class RoundtableReq(BaseModel):
    group_key: str
    request: str  # 圆桌议题（作为开场 human 消息进 history）
    roster: list[str]  # contact_ids（到场成员）
    max_turns_per_human: int | None = None  # 预算闸（默认用 GroupState 缺省）


class RecipeRunReq(BaseModel):
    """跑库内任意配方（S5.4.2b）：按 recipe_id 取 graph→编译→流式跑。"""

    recipe_id: str
    group_key: str
    request: str  # 议题/需求（作为开场 human 消息进 history）
    roster: list[str]  # contact_ids（到场成员）
    max_turns_per_human: int | None = None


class RoundtableResumeReq(BaseModel):
    """续场 resume：按当前暂停点选字段——human_gate 用 interject；clarify 用 answer/skip。"""

    interject: str | None = None  # human_gate：插话文本；None=不插话继续讨论
    answer: str | None = None  # clarify：答复澄清问
    skip: bool = False  # clarify：跳过澄清
    end: bool = False  # human_gate：手动结束并主笔综合（S3.6h）


class InterjectReq(BaseModel):
    text: str  # 异步插话：写入 pending_human，下次 human_gate 消化


class RelayInboundReq(BaseModel):
    """group_relay 桥转发的群消息（§2.1 InboundMsg）；只有人类消息触发圆桌。"""

    group_key: str
    text: str = ""
    sender_kind: str = "human"
    platform: str = ""
    sender_id: str = ""
    sender_name: str = ""
    native_msg_id: str = ""
    ts: float = 0.0


class ContactIn(BaseModel):
    id: str
    name: str
    title: str = ""
    persona_style: str = ""
    base_stance: str = ""
    bot_ref: str = ""  # AstrBot platform 实例 id（出站以该 bot 身份发言，S4.3）


class RecipeIn(BaseModel):
    """配方库写入（S5.4.2a）：graph 是图原生 DAG（nodes/edges），写时经 validate_recipe 校验。"""

    id: str
    name: str = ""
    graph: dict


class RecipeValidateReq(BaseModel):
    """L4 画布实时校验（S5.4.3c）：只校验 graph，不落库。"""

    graph: dict


class RecipeAutoReq(BaseModel):
    """L3 让 AI 按任务搭一张配方（S5.5）。"""

    task: str
    roster: list[str] = []


def _cfg(group_key: str) -> dict:
    return {"configurable": {"thread_id": group_key}}


def _primitive_dict(prim) -> dict:
    """把一个原语的 PrimitiveSpec 投影成机读 dict（S5.4.3a，供 L4 画布建卡片/连线合法性）。"""
    s = prim.spec
    return {
        "name": s.name,
        "kind": s.kind,
        "reads": list(s.reads),
        "writes": list(s.writes),
        "needs": list(s.needs),
        "emits": list(s.emits),
        "budget": (
            {"count": s.budget.count, "limit": s.budget.limit, "reason": s.budget.reason}
            if s.budget
            else None
        ),
        "args": None,  # spec.args 全 None（args schema 留后）
    }


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


# 圆桌进度反馈（S3.6g）：在静默推理段前注入 status，前端显示进度而非卡死。
_RT_START_STATUS = {"type": "status", "stage": "preparing"}  # leg 开头（澄清/框定/调度）
_RT_FOLLOW_STATUS = {"framed": {"type": "status", "stage": "thinking"}}  # 框定后→发言者思考


def _sse_from_events(
    graph, stream_input, cfg, *, start_status=None, follow=None
) -> StreamingResponse:
    """圆桌 SSE：消费 transport 无关的 `iter_events`（§6.12），把中性事件转 SSE dict + 心跳。

    stream_input 可是初始 state（起场）或 `Command(resume=...)`（续场）。图在 interrupt
    （human_gate/clarify）处自然暂停 → 发对应 type 事件后收 done。

    进度反馈（S3.6g）：`start_status` 在 leg 开头先发；`follow`（事件 type→后续 status）
    在某事件后补发——覆盖"框定后→发言者思考"等静默段，前端据此显示进度而非像卡死。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            if start_status:
                await queue.put(start_status)
            async for ev in iter_events(graph, stream_input, cfg):
                d = ev.to_dict()
                await queue.put(d)
                if follow and d.get("type") in follow:
                    await queue.put(follow[d["type"]])
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
                    yield ": heartbeat\n\n"  # 保活：turn 思考/长沉默时防断连
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


async def _require_thread(graph, group_key: str) -> None:
    """resume 前置校验：线程须已有 checkpoint（即先 /inbound 过）。"""
    snap = await graph.aget_state(_cfg(group_key))
    if not snap.values:
        raise HTTPException(
            status_code=404,
            detail=f"group {group_key} not found; call /inbound first",
        )


def _resume_payload(req: "RoundtableResumeReq") -> dict:
    """续场字段 → 非空 resume dict（clarify skip/answer；human_gate end/interject）。"""
    if req.skip:
        return {"skip": True}
    if req.answer is not None:
        return {"answer": req.answer}
    if req.end:
        return {"end": True}
    return {"interject": req.interject}


async def _graph_for(app_state, recipe_id: str):
    """按会话 recipe_id 取对应图（S5.7b，继续/重试共用）：空→默认圆桌；否则库内配方重编译。"""
    if not recipe_id:
        return app_state.roundtable_graph
    async with app_state.session_factory() as s:
        rec = await s.get(Recipe, recipe_id)
    if rec is None:
        return app_state.roundtable_graph  # 配方已删 → 回退（仍能续 state）
    return compile_recipe(rec.graph, app_state.saver, deps=app_state.recipe_deps)


def create_app(
    *,
    checkpointer=None,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
    clarify_assess: ClarifyFn | None = None,
    extract: ClaimExtractor | None = None,
    pick: PickFn | None = None,
    planner: PlanFn | None = None,
    compose: ComposeFn | None = None,
    recipe_selector: RecipeSelector | None = None,
    recipe_planner: RecipePlanner | None = None,
    bridge_url: str = "http://127.0.0.1:9876",
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
        await seed_builtin_recipes(sf)  # S5.4.2a：四内置配方幂等 seed 进库
        pp = persona_provider or persona_provider_from(sf)
        ra = reputation_adjuster or reputation_adjuster_from(sf)
        app.state.persona_provider = pp
        app.state.reputation_adjuster = ra

        def _build_graphs(saver) -> None:
            # 两张配方图共享同一 checkpointer（不同 group_key 互不干扰）。
            app.state.graph = build_fanout_recipe(
                saver,
                assign=assign,
                generate=generate,
                persona_provider=pp,
                reputation_adjuster=ra,
                clarify_assess=clarify_assess,
            )
            # 圆桌：human_in_loop=True 每轮停在 human_gate（让位窗口，S3.6d resume 续）。
            app.state.roundtable_graph = build_roundtable_recipe(
                saver,
                assign=assign,
                generate=generate,
                persona_provider=pp,
                extract=extract,
                pick=pick,
                clarify_assess=clarify_assess,
                compose=compose,
                human_in_loop=True,
            )
            # S4.4：telegram 驱动器——入站起圆桌、后台多轮、出站经桥推回群。
            # relay 专用图：clarify 关闭（群里自动模式无人答澄清，且少一个会空的结构化调用）。
            relay_graph = build_roundtable_recipe(
                saver,
                assign=assign,
                generate=generate,
                persona_provider=pp,
                extract=extract,
                pick=pick,
                clarify_assess=None,
                compose=compose,
                human_in_loop=True,
            )
            outbound = OutboundClient(bridge_url, bot_ref_provider_from(sf))
            app.state.relay_driver = RelayDriver(
                relay_graph, outbound, roster_provider_from(sf)
            )
            # S5.4.2b：存 saver + live deps，供 /recipe/run 按需编译任意库内配方。
            app.state.saver = saver
            app.state.recipe_deps = {
                "assign": assign,
                "generate": generate,
                "persona_provider": pp,
                "reputation_adjuster": ra,
                "extract": extract,
                "pick": pick,
                "planner": planner,
                "compose": compose,
                "assess": clarify_assess,
            }

        try:
            if checkpointer is not None:
                _build_graphs(checkpointer)
                yield
            else:
                async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
                    _build_graphs(saver)
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

    @app.get("/primitives")
    async def list_primitives():
        """L4 画布卡片库（S5.4.3a，§6.16）：暴露 registry 每原语的机读契约。"""
        return [_primitive_dict(p) for p in REGISTRY.values()]

    @app.post("/recipe/validate")
    async def recipe_validate_ep(req: RecipeValidateReq):
        """L4 画布实时校验（S5.4.3c）：返回人话错误列表（空=合法），复用 1c。"""
        return {"errors": validate_recipe(req.graph)}

    @app.post("/recipe/select")
    async def recipe_select_ep(req: RecipeSelectReq):
        """L2 荐配方（§6.13）：按任务返回 roundtable|fanout（未配置 selector→默认）。"""
        choice = await select_recipe(req.task, selector=recipe_selector)
        return {"recipe": choice.recipe, "reason": choice.reason}

    @app.post("/recipe/auto")
    async def recipe_auto_ep(req: RecipeAutoReq, request: Request):
        """L3（S5.5）：AI 按任务产出一张 DAG，存进库（builtin=False）→ 可在画布看/改/跑。"""
        name, graph = await plan_recipe(req.task, req.roster, planner=recipe_planner)
        errs = validate_recipe(graph)
        if errs:  # assemble 应恒合法；兜底防御
            raise HTTPException(status_code=500, detail=errs)
        rid = f"rcp-{uuid4().hex[:8]}"
        graph = {**graph, "recipe": rid}
        async with request.app.state.session_factory() as s:
            obj = Recipe(id=rid, name=name, builtin=False, graph=graph)
            s.add(obj)
            await s.commit()
            return obj

    @app.post("/recipe/run")
    async def recipe_run(req: RecipeRunReq, request: Request):
        """跑库内任意配方（S5.4.2b，§6.16）：取 graph→validate→compile(live deps)→SSE 流式。

        图在 interrupt（human_gate/clarify）处自然暂停 → 发对应事件后收 done；自治图（如 auto）
        一气呵成跑到 output→END。续场 resume 复用现有 `/roundtable/{key}/resume/stream`（共享 saver）。
        """
        async with request.app.state.session_factory() as s:
            rec = await s.get(Recipe, req.recipe_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"recipe {req.recipe_id} not found")
        errs = validate_recipe(rec.graph)
        if errs:
            raise HTTPException(status_code=422, detail=errs)
        graph = compile_recipe(
            rec.graph, request.app.state.saver, deps=request.app.state.recipe_deps
        )
        state_in: dict = {
            "group_key": req.group_key,
            "roster": [AgentSlot(contact_id=c) for c in req.roster],
            "history": [Msg(sender_id="human", sender_kind="human", text=req.request)],
            "pending_human": None,
        }
        if req.max_turns_per_human is not None:
            state_in["max_turns_per_human"] = req.max_turns_per_human
        await upsert_conversation(
            request.app.state.session_factory, req.group_key, req.request, req.recipe_id
        )
        return _sse_from_events(
            graph,
            state_in,
            _cfg(req.group_key),
            start_status=_RT_START_STATUS,
            follow=_RT_FOLLOW_STATUS,
        )

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

    @app.post("/roundtable/stream")
    async def roundtable_stream(req: RoundtableReq, request: Request):
        """SSE 起一场圆桌：议题作开场 human 消息进 history、pending_human=None（入口约定）。

        图跑到第一轮发言后停在 human_gate（human_in_loop=True）——SSE 出 framed/delta/turn
        后发 human_gate 事件收束；续场/插话见 S3.6d。信心不足则先出 clarify 事件。
        """
        graph = request.app.state.roundtable_graph
        state_in: dict = {
            "group_key": req.group_key,
            "roster": [AgentSlot(contact_id=c) for c in req.roster],
            "history": [Msg(sender_id="human", sender_kind="human", text=req.request)],
            "pending_human": None,
        }
        if req.max_turns_per_human is not None:
            state_in["max_turns_per_human"] = req.max_turns_per_human
        await upsert_conversation(request.app.state.session_factory, req.group_key, req.request)
        return _sse_from_events(
            graph,
            state_in,
            _cfg(req.group_key),
            start_status=_RT_START_STATUS,
            follow=_RT_FOLLOW_STATUS,
        )

    @app.post("/roundtable/{key}/resume/stream")
    async def roundtable_resume_stream(key: str, req: RoundtableResumeReq, request: Request):
        """续一场暂停的圆桌：按字段转 Command(resume=...)，SSE 出后续轮次到下一暂停点。

        resume 必须**非空**（LangGraph 把 falsy resume 当未恢复会重触发 interrupt）：
        clarify→{"skip":True}|{"answer":text}；human_gate→{"interject":text|null}（含 key 非空）。
        """
        graph = request.app.state.roundtable_graph
        cfg = _cfg(key)
        await _require_thread(graph, key)
        return _sse_from_events(
            graph,
            Command(resume=_resume_payload(req)),
            cfg,
            start_status=_RT_START_STATUS,
            follow=_RT_FOLLOW_STATUS,
        )

    @app.post("/session/{key}/resume/stream")
    async def session_resume_stream(key: str, req: RoundtableResumeReq, request: Request):
        """通用续场（S5.7b）：按会话 recipe_id 取对应图，在同一 thread 上续场（自定义配方也能续）。"""
        async with request.app.state.session_factory() as s:
            conv = await s.get(Conversation, key)
        recipe_id = conv.recipe_id if conv else ""
        graph = await _graph_for(request.app.state, recipe_id)
        await _require_thread(graph, key)
        return _sse_from_events(
            graph,
            Command(resume=_resume_payload(req)),
            _cfg(key),
            start_status=_RT_START_STATUS,
            follow=_RT_FOLLOW_STATUS,
        )

    @app.post("/relay/inbound")
    async def relay_inbound(req: RelayInboundReq, request: Request):
        """group_relay 桥入站：人类群消息 → 起/续圆桌（后台多轮、出站推回群）。"""
        if req.sender_kind != "human" or not req.text.strip():
            return {"status": "ignored"}
        return await request.app.state.relay_driver.handle_inbound(req.group_key, req.text)

    @app.post("/roundtable/{key}/interject")
    async def roundtable_interject(key: str, req: InterjectReq, request: Request):
        """异步插话通道：外部写 pending_human；下次 human_gate resume 时消化（改向）。"""
        graph = request.app.state.roundtable_graph
        await _require_thread(graph, key)
        await graph.aupdate_state(
            _cfg(key),
            {"pending_human": Msg(sender_id="human", sender_kind="human", text=req.text)},
        )
        return {"ok": True, "pending": req.text}

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

    # ---- 配方库 CRUD（S5.4.2a，§6.16）----

    @app.get("/recipes")
    async def list_recipes(request: Request):
        async with request.app.state.session_factory() as s:
            return (await s.exec(select(Recipe))).all()

    @app.get("/recipes/{rid}")
    async def get_recipe(rid: str, request: Request):
        async with request.app.state.session_factory() as s:
            obj = await s.get(Recipe, rid)
            if obj is None:
                raise HTTPException(status_code=404, detail=f"recipe {rid} not found")
            return obj

    @app.post("/recipes")
    async def create_recipe(r: RecipeIn, request: Request):
        errs = validate_recipe(r.graph)
        if errs:
            raise HTTPException(status_code=422, detail=errs)  # 写时校验（§6.16 C，复用 1c）
        async with request.app.state.session_factory() as s:
            if await s.get(Recipe, r.id) is not None:
                raise HTTPException(status_code=409, detail=f"recipe {r.id} exists")
            obj = Recipe(id=r.id, name=r.name or r.id, builtin=False, graph=r.graph)
            s.add(obj)
            await s.commit()
            return obj

    @app.put("/recipes/{rid}")
    async def update_recipe(rid: str, r: RecipeIn, request: Request):
        errs = validate_recipe(r.graph)
        if errs:
            raise HTTPException(status_code=422, detail=errs)
        async with request.app.state.session_factory() as s:
            obj = await s.get(Recipe, rid)
            if obj is None:
                raise HTTPException(status_code=404, detail=f"recipe {rid} not found")
            if obj.builtin:
                raise HTTPException(status_code=403, detail=f"builtin recipe {rid} is read-only")
            obj.name = r.name or rid
            obj.graph = r.graph
            s.add(obj)
            await s.commit()
            return obj

    @app.delete("/recipes/{rid}")
    async def delete_recipe(rid: str, request: Request):
        async with request.app.state.session_factory() as s:
            obj = await s.get(Recipe, rid)
            if obj is None:
                raise HTTPException(status_code=404, detail=f"recipe {rid} not found")
            if obj.builtin:
                raise HTTPException(status_code=403, detail=f"builtin recipe {rid} cannot be deleted")
            await s.delete(obj)
            await s.commit()
            return {"deleted": rid}

    # ---- 会话历史（S5.7a，§6.17）----

    @app.get("/conversations")
    async def list_conversations(request: Request):
        """会话索引（近→远）。消息本体在 checkpointer，这里只列标题/时间。"""
        async with request.app.state.session_factory() as s:
            return (
                await s.exec(select(Conversation).order_by(Conversation.created_at.desc()))
            ).all()

    @app.get("/conversations/{key}")
    async def get_conversation(key: str, request: Request):
        """从 checkpointer 读一场会话的 history/output/roster + resumable（snap.next 非空=可续）。

        读 state 与图无关（同 saver+thread_id），用 roundtable_graph 读即可；续跑取对应图 = S5.7b。
        """
        async with request.app.state.session_factory() as s:
            conv = await s.get(Conversation, key)
        snap = await request.app.state.roundtable_graph.aget_state(_cfg(key))
        vals = snap.values or {}
        if conv is None and not vals:
            raise HTTPException(status_code=404, detail=f"conversation {key} not found")
        return {
            "id": key,
            "title": conv.title if conv else "",
            "recipe_id": conv.recipe_id if conv else "",
            "created_at": conv.created_at if conv else None,
            "history": vals.get("history", []),
            "output": vals.get("output"),
            "roster": vals.get("roster", []),
            "resumable": bool(snap.next),
        }

    return app
