"""S3.2 判据：decide_next 三分支返回正确决策类型；到 max_turns_per_human 必返 Stop。
moderator_llm_pick 复用 structured_invoke（假 model 走 text_json，离线）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.nodes.schedule import (
    NextSpeaker,
    Stop,
    YieldToHuman,
    decide_next,
    moderator_llm_pick,
    schedule,
)
from app.state import AgentSlot, GroupState, Msg


def _state(**kw) -> GroupState:
    base = dict(
        group_key="g",
        roster=[AgentSlot(contact_id="A"), AgentSlot(contact_id="B")],
        history=[Msg(sender_id="u", sender_kind="human", text="Q")],
    )
    base.update(kw)
    return GroupState(**base)


async def _pick_A(state) -> NextSpeaker:
    return NextSpeaker(contact_id="A")


# ---- decide_next 三分支 ----


async def test_pending_human_yields():
    state = _state(pending_human=Msg(sender_id="u", sender_kind="human", text="插话"))
    d = await decide_next(state, pick=_pick_A)
    assert isinstance(d, YieldToHuman)


async def test_budget_gate_stops():
    state = _state(turns_since_human=6, max_turns_per_human=6)
    d = await decide_next(state, pick=_pick_A)
    assert isinstance(d, Stop) and d.reason == "budget"


async def test_human_priority_over_budget():
    # 人插话即便预算已耗尽也优先让位（precedence）
    state = _state(
        turns_since_human=99,
        max_turns_per_human=6,
        pending_human=Msg(sender_id="u", sender_kind="human", text="插话"),
    )
    assert isinstance(await decide_next(state, pick=_pick_A), YieldToHuman)


async def test_otherwise_moderator_picks():
    d = await decide_next(_state(turns_since_human=1), pick=_pick_A)
    assert isinstance(d, NextSpeaker) and d.contact_id == "A"


# ---- moderator_llm_pick（结构化，假 model）----


class _FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content

    async def astream(self, messages, config=None):  # noqa: ANN001 - 测试桩
        yield AIMessageChunk(content=self.content)


async def test_moderator_pick_speaker():
    pick = moderator_llm_pick(_FakeModel('{"stop": false, "next_contact_id": "B"}'))
    d = await pick(_state())
    assert isinstance(d, NextSpeaker) and d.contact_id == "B"


async def test_moderator_pick_stop():
    pick = moderator_llm_pick(_FakeModel('{"stop": true, "next_contact_id": null}'))
    d = await pick(_state())
    assert isinstance(d, Stop) and d.reason == "moderator"


async def test_moderator_pick_invalid_id_falls_back_to_first():
    pick = moderator_llm_pick(_FakeModel('{"stop": false, "next_contact_id": "Z"}'))
    d = await pick(_state())
    assert isinstance(d, NextSpeaker) and d.contact_id == "A"  # 非 roster → 兜底首位


# ---- schedule 节点：决策落成 state delta（供 S3.3 路由）----


async def test_schedule_node_maps_decision_to_delta():
    out = await schedule(_state(turns_since_human=1), pick=_pick_A)
    assert out == {"next_speaker": "A", "next_decision": "next_speaker", "stop_reason": None}

    out_stop = await schedule(_state(turns_since_human=6, max_turns_per_human=6), pick=_pick_A)
    assert out_stop["next_decision"] == "stop" and out_stop["stop_reason"] == "budget"
    assert out_stop["next_speaker"] is None
