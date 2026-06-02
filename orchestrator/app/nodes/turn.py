"""S3.1: TURN 节点——单个 agent 发言（看得到上文 history），产出追加 state.history。

谁发言由 `state.next_speaker`（contact_id）指定——S3.2 SCHEDULE 决定，S3.1 可手工设。
复用 `generate.py` 的混合身份生成（基础人设 + 临场维度 + 群历史 §4），把生成文本包成
一条 `ai` Msg 追加进 `history`，并 `turns_since_human += 1`。
不做：调度决策（S3.2）、并行（扇出各管各的）。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from ..llm import make_chat_model
from ..state import AgentSlot, GroupState, Msg
from ._common import request_text
from .generate import GenerateFn, PersonaProvider, default_generator


def _speaker_slot(state: GroupState) -> AgentSlot | None:
    """取本步发言人的 slot：roster 里找 next_speaker，找不到则临时建（保底）。"""
    cid = state.next_speaker
    if cid is None:
        return None
    return next(
        (s for s in state.roster if s.contact_id == cid),
        AgentSlot(contact_id=cid),
    )


async def turn(
    state: GroupState,
    *,
    model: ChatOpenAI | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
) -> dict:
    """让 `next_speaker` 发一次言，追加进 history，turns_since_human += 1。

    生成时把当前 `history` 喂给 generate → 后发言的 agent 看得到先发言的内容（上文可见性）。
    无 next_speaker 则空步（SCHEDULE 未决/让位人类，留 S3.2/S3.4）。
    """
    slot = _speaker_slot(state)
    if slot is None:
        return {}
    gen = generate or default_generator(model or make_chat_model(), persona_provider)
    request = request_text(state)
    cand = await gen(slot, request, state.history, state.claims)
    msg = Msg(
        sender_id=slot.contact_id,
        sender_kind="ai",
        text=cand.text,
        dimension=slot.dimension,
    )
    return {
        "history": [*state.history, msg],
        "turns_since_human": state.turns_since_human + 1,
    }
