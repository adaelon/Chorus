"""S3.1 判据：连续两次 TURN，第二次生成时能看到第一次发言（上文可见性）；
history 追加 + turns_since_human 累积。注入假 generate，离线确定性。
"""

from __future__ import annotations

from app.nodes.turn import turn
from app.state import AgentSlot, Candidate, GroupState, Msg


def _make_capturing_gen(seen: list[list[Msg]]):
    async def gen(slot: AgentSlot, request: str, history: list[Msg]) -> Candidate:
        seen.append(list(history))  # 记录本次发言看到的上文
        return Candidate(
            contact_id=slot.contact_id,
            dimension=slot.dimension,
            text=f"{slot.contact_id} 的发言",
        )

    return gen


def _state() -> GroupState:
    return GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id="A", dimension="成本"), AgentSlot(contact_id="B", dimension="文笔")],
        history=[Msg(sender_id="u", sender_kind="human", text="便利店选题")],
        pending_human=Msg(sender_id="u", sender_kind="human", text="便利店选题"),
        next_speaker="A",
    )


async def test_turn_appends_message_and_increments():
    out = await turn(_state(), generate=_make_capturing_gen([]))
    assert out["turns_since_human"] == 1
    last = out["history"][-1]
    assert last.sender_id == "A" and last.sender_kind == "ai"
    assert last.text == "A 的发言" and last.dimension == "成本"


async def test_second_turn_sees_first_turn_speech():
    seen: list[list[Msg]] = []
    gen = _make_capturing_gen(seen)

    state = _state()
    out1 = await turn(state, generate=gen)
    # 用 A 发言后的 state 跑第二次（轮到 B）
    state2 = state.model_copy(update={**out1, "next_speaker": "B"})
    out2 = await turn(state2, generate=gen)

    # B 发言时看到的上文里含 A 刚说的话（上文可见性）
    assert any(m.sender_id == "A" and m.text == "A 的发言" for m in seen[1])
    # turns_since_human 跨步累积
    assert out2["turns_since_human"] == 2
    assert [m.sender_id for m in out2["history"]] == ["u", "A", "B"]


async def test_no_next_speaker_is_noop():
    state = _state().model_copy(update={"next_speaker": None})
    out = await turn(state, generate=_make_capturing_gen([]))
    assert out == {}
