"""S1.5 判据：pick/eliminate/reassign 各一条，state 正确变更；reassign 产出含 point。"""

from __future__ import annotations

from app.nodes.curate import Eliminate, Pick, Reassign, curate
from app.state import AgentSlot, Candidate, GroupState, Msg


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    # 回显 request（含被 reassign 注入的 point），便于断言
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}] {request}")


def _state() -> GroupState:
    return GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id="A", dimension="成本"), AgentSlot(contact_id="B", dimension="文笔")],
        candidates=[
            Candidate(contact_id="A", dimension="成本", text="A 的方案"),
            Candidate(contact_id="B", dimension="文笔", text="B 的方案"),
        ],
        pending_human=Msg(sender_id="u", sender_kind="human", text="便利店选题"),
    )


async def test_pick_adds_to_picked():
    out = await curate(_state(), [Pick(contact_id="A", point="降本要点")], generate=_fake_gen)
    assert any(c.contact_id == "A" and c.text == "降本要点" for c in out["picked"])


async def test_eliminate_removes_candidate():
    out = await curate(_state(), [Eliminate(contact_id="A")], generate=_fake_gen)
    ids = {c.contact_id for c in out["candidates"]}
    assert "A" not in ids and "B" in ids


async def test_reassign_generates_new_candidate_with_point():
    point = "A 提的现金流视角"
    out = await curate(_state(), [Reassign(point=point, executor_id="B")], generate=_fake_gen)
    b_cands = [c for c in out["candidates"] if c.contact_id == "B"]
    # B 多出一份新候选，且文本含被交办的 point
    assert len(b_cands) == 2
    assert any(point in c.text for c in b_cands)
