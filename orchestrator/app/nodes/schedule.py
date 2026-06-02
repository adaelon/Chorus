"""S3.2: SCHEDULE 节点——主持人调度，决定下一个谁说 / 让位人类 / 停（§3.2）。

`decide_next` 的优先级（§3.2）：
  1) `pending_human` 在 → YieldToHuman（人插话优先，注入通道留 S3.4）
  2) 预算闸：`turns_since_human >= max_turns_per_human` → Stop(reason="budget")
  3) 否则 `moderator_llm_pick`：一次廉价结构化 LLM 调用选下一发言人 / 建议停

决策是一个 union；`moderator_llm_pick` 复用 `structured_invoke`（§6.9）。`pick` 可注入以离线测试。
`schedule` 节点把决策落成 state delta（`next_speaker`/`next_decision`/`stop_reason`），
供圆桌配方（S3.3）的条件边路由——节点本身不耦合具体配方的节点命名。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..llm import make_chat_model
from ..state import GroupState
from ..structured import structured_invoke


class NextSpeaker(BaseModel):
    kind: Literal["next_speaker"] = "next_speaker"
    contact_id: str


class YieldToHuman(BaseModel):
    kind: Literal["yield_to_human"] = "yield_to_human"


class Stop(BaseModel):
    kind: Literal["stop"] = "stop"
    reason: str = ""


SchedulerDecision = NextSpeaker | YieldToHuman | Stop

# 选下一发言人的策略：(state) -> 决策。默认 moderator_llm_pick，可注入假实现离线测试。
PickFn = Callable[[GroupState], Awaitable[SchedulerDecision]]


class _ModeratorChoice(BaseModel):
    """主持人一次结构化选择：让谁说，或建议停止。"""

    stop: bool = False  # 讨论已充分 → 建议停止
    next_contact_id: str | None = None  # 否则：下一发言人的 contact_id


def moderator_llm_pick(model: ChatOpenAI) -> PickFn:
    """主持人 LLM 选下一发言人（或建议停），复用 structured_invoke（§6.9）。"""

    async def pick(state: GroupState) -> SchedulerDecision:
        ids = [s.contact_id for s in state.roster]
        if not ids:
            return Stop(reason="empty_roster")
        points = "\n".join(f"- {c.speaker_id}：{c.text}" for c in state.claims) or "（暂无）"
        recent = "\n".join(
            f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in state.history[-6:]
        ) or "（暂无）"
        system = (
            "你是圆桌主持人。基于讨论进展，决定下一个发言的成员（推动讨论、覆盖未充分的角度），"
            "或在讨论已充分时建议停止。只能从给定成员里选。"
        )
        user = f"到场成员：{ids}\n已有要点：\n{points}\n最近发言：\n{recent}"
        choice = await structured_invoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)], _ModeratorChoice
        )
        if choice.stop:
            return Stop(reason="moderator")
        cid = choice.next_contact_id if choice.next_contact_id in ids else ids[0]
        return NextSpeaker(contact_id=cid)

    return pick


async def decide_next(
    state: GroupState,
    *,
    pick: PickFn | None = None,
    model: ChatOpenAI | None = None,
) -> SchedulerDecision:
    """§3.2 调度决策：人优先 → 预算闸 → 主持人选人。"""
    if state.pending_human is not None:
        return YieldToHuman()
    if state.turns_since_human >= state.max_turns_per_human:
        return Stop(reason="budget")
    chooser = pick or moderator_llm_pick(model or make_chat_model())
    return await chooser(state)


async def schedule(
    state: GroupState,
    *,
    pick: PickFn | None = None,
    model: ChatOpenAI | None = None,
) -> dict:
    """SCHEDULE 节点：跑 decide_next，把决策落成 state delta（供 S3.3 条件边路由）。"""
    decision = await decide_next(state, pick=pick, model=model)
    if isinstance(decision, NextSpeaker):
        return {
            "next_speaker": decision.contact_id,
            "next_decision": "next_speaker",
            "stop_reason": None,
        }
    if isinstance(decision, YieldToHuman):
        return {"next_speaker": None, "next_decision": "yield_to_human", "stop_reason": None}
    return {"next_speaker": None, "next_decision": "stop", "stop_reason": decision.reason}
