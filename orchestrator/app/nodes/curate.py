"""S1.5: CURATE 节点——把人工策展指令 apply 到 state。

三类指令（来自人 / 前端，已是结构化输入，无需 LLM 解析）：
  pick      选中某 agent 的候选 / 其中一个点 → 进 picked
  eliminate 淘汰某 agent 的候选 → 从 candidates 移除（软，仅本场；信誉写入留 S2.3）
  reassign  把某个点交给 executor 写 → 触发对其一次定向再生成，追加新候选

S3.0：CURATE 进图，用 LangGraph `interrupt` 做人在环（暂停—等人—resume，多轮循环）。
`curate()` 仍是纯 apply（无副作用之外不碰图），`curate_interrupt_node` 在其上加打断/循环。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel

from ..llm import make_chat_model
from ..state import AgentSlot, Candidate, GroupState
from ._common import request_text
from .generate import GenerateFn, PersonaProvider, default_generator


class Pick(BaseModel):
    kind: Literal["pick"] = "pick"
    contact_id: str
    point: str | None = None  # 选其中一个点；None=整份候选


class Eliminate(BaseModel):
    kind: Literal["eliminate"] = "eliminate"
    contact_id: str


class Reassign(BaseModel):
    kind: Literal["reassign"] = "reassign"
    point: str  # 要落地的点（常来自另一个 agent A）
    executor_id: str  # 交给谁写（B）


CurateCommand = Pick | Eliminate | Reassign

_COMMAND_TYPES = {"pick": Pick, "eliminate": Eliminate, "reassign": Reassign}


def parse_command(d) -> CurateCommand:
    """把 resume payload 里的命令（dict 或已构造对象）规整成命令模型。"""
    if isinstance(d, (Pick, Eliminate, Reassign)):
        return d
    kind = d["kind"]
    return _COMMAND_TYPES[kind](**d)

# 信誉软加权（§8.4）：人工信号调整权重，非处决。
ReputationAdjuster = Callable[[str, float], Awaitable[None]]
PICK_DELTA = 1.0
ELIMINATE_DELTA = -1.0


def _find_candidate(candidates: list[Candidate], contact_id: str) -> Candidate | None:
    return next((c for c in candidates if c.contact_id == contact_id), None)


def _find_slot(roster: list[AgentSlot], contact_id: str) -> AgentSlot | None:
    return next((s for s in roster if s.contact_id == contact_id), None)


async def curate(
    state: GroupState,
    commands: Sequence[CurateCommand],
    *,
    model: ChatOpenAI | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
) -> dict:
    """按顺序 apply 指令，返回更新后的 candidates / picked。

    pick/eliminate 经 reputation_adjuster 做软加权（§8.4，非处决、可逆）。
    """
    candidates = list(state.candidates)
    picked = list(state.picked)
    gen = generate or default_generator(model or make_chat_model(), persona_provider)
    request = request_text(state)

    for cmd in commands:
        if isinstance(cmd, Pick):
            cand = _find_candidate(candidates, cmd.contact_id)
            text = cmd.point if cmd.point is not None else (cand.text if cand else "")
            picked.append(
                Candidate(
                    contact_id=cmd.contact_id,
                    dimension=cand.dimension if cand else None,
                    text=text,
                )
            )
            if reputation_adjuster:
                await reputation_adjuster(cmd.contact_id, PICK_DELTA)
        elif isinstance(cmd, Eliminate):
            candidates = [c for c in candidates if c.contact_id != cmd.contact_id]
            if reputation_adjuster:
                await reputation_adjuster(cmd.contact_id, ELIMINATE_DELTA)
        elif isinstance(cmd, Reassign):
            slot = _find_slot(state.roster, cmd.executor_id) or AgentSlot(
                contact_id=cmd.executor_id
            )
            augmented = f"{request}\n\n请基于以下要点来写：{cmd.point}"
            candidates.append(await gen(slot, augmented, state.history, state.claims))

    return {"candidates": candidates, "picked": picked}


def _curate_payload(state: GroupState) -> dict:
    return {
        "type": "curate",
        "candidates": [c.model_dump() for c in state.candidates],
        "picked": [c.model_dump() for c in state.picked],
    }


async def curate_interrupt_node(
    state: GroupState,
    *,
    model: ChatOpenAI | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
) -> dict:
    """图节点：暂停（interrupt）暴露当前候选给人工，按 resume 指令循环或转 synthesize。

    resume 协议（service 转发）：
      {"action": "curate", "commands": [...]} → apply 后回到本节点（再次 interrupt，多轮）
      {"action": "synthesize"}                → 转 synthesize（终端节点）

    **S5.4.0b/c 路由出节点（§6.16 A.3）**：interrupt（暂停）留在节点，跳转（goto）抽到边——
    本节点只写 state delta + `next_decision∈{curate,synthesize}`，由配方条件边路由
    （curate→curate 自循环 / synthesize→synthesize）。不再焊死拓扑，可被 L4 用户重新接线。
    """
    resume = interrupt(_curate_payload(state))
    action = resume.get("action", "synthesize") if isinstance(resume, dict) else "synthesize"
    if action != "curate":
        return {"next_decision": "synthesize"}
    commands = [parse_command(d) for d in (resume.get("commands") or [])]
    delta = await curate(
        state,
        commands,
        model=model,
        generate=generate,
        persona_provider=persona_provider,
        reputation_adjuster=reputation_adjuster,
    )
    return {**delta, "next_decision": "curate"}
