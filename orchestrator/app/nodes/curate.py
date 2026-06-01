"""S1.5: CURATE 节点——把人工策展指令 apply 到 state。

三类指令（来自人 / 前端，已是结构化输入，无需 LLM 解析）：
  pick      选中某 agent 的候选 / 其中一个点 → 进 picked
  eliminate 淘汰某 agent 的候选 → 从 candidates 移除（软，仅本场；信誉写入留 S2.3）
  reassign  把某个点交给 executor 写 → 触发对其一次定向再生成，追加新候选
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from langchain_openai import ChatOpenAI
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
) -> dict:
    """按顺序 apply 指令，返回更新后的 candidates / picked。"""
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
        elif isinstance(cmd, Eliminate):
            candidates = [c for c in candidates if c.contact_id != cmd.contact_id]
        elif isinstance(cmd, Reassign):
            slot = _find_slot(state.roster, cmd.executor_id) or AgentSlot(
                contact_id=cmd.executor_id
            )
            augmented = f"{request}\n\n请基于以下要点来写：{cmd.point}"
            candidates.append(await gen(slot, augmented, state.history))

    return {"candidates": candidates, "picked": picked}
