"""S3.1: TURN 节点——单个 agent 发言（看得到上文 history），产出追加 state.history。

谁发言由 `state.next_speaker`（contact_id）指定——S3.2 SCHEDULE 决定，S3.1 可手工设。
复用 `generate.py` 的混合身份生成（基础人设 + 临场维度 + 群历史 §4），把生成文本包成
一条 `ai` Msg 追加进 `history`，并 `turns_since_human += 1`。
不做：调度决策（S3.2）、并行（扇出各管各的）。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from ..llm import make_chat_model
from ..run_ctx import current_group_key
from ..state import AgentSlot, GroupState, Msg
from ._common import request_text
from .extract import ClaimExtractor, default_claim_extractor
from .generate import GenerateFn, ModelProvider, PersonaProvider, default_generator


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
    model_provider: ModelProvider | None = None,
    extract: ClaimExtractor | None = None,
) -> dict:
    """让 `next_speaker` 发一次言，追加进 history，turns_since_human += 1，并中立提点入账本。

    生成时把 `history`(近场原文) + `claims`(远场点账本) 喂给 generate（投影器 §6.11）→
    后发言者既看得到近期原文、也看得到全程的"点"。发言后中立提点追加进 `state.claims`。
    无 next_speaker 则空步（SCHEDULE 未决/让位人类，留 S3.2/S3.4）。
    """
    slot = _speaker_slot(state)
    if slot is None:
        return {}
    current_group_key.set(state.group_key)  # S7.3b：供 follow-bot 模型按 umo 委托
    gen = generate or default_generator(
        model or make_chat_model(), persona_provider, model_provider=model_provider
    )
    ext = extract or default_claim_extractor(model or make_chat_model())
    request = request_text(state)
    if state.directed_active:
        # §6.20：真人点名（@）要这位按指令修改自己的发言——框定为定向修订，而非泛泛接话。
        request = f"真人点名要你按下面的指令修改你的发言（只针对你）：\n{request}"
    cand = await gen(slot, request, state.history, state.claims)
    msg = Msg(
        sender_id=slot.contact_id,
        sender_kind="ai",
        text=cand.text,
        dimension=slot.dimension,
    )
    turn_idx = state.turns_since_human + 1
    new_claims = await ext(cand.text, slot.contact_id, turn_idx)
    return {
        "history": [*state.history, msg],
        "turns_since_human": turn_idx,
        "claims": [*state.claims, *new_claims],
    }
