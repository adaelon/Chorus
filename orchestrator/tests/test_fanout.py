"""S1.3 判据：FANOUT 返回 N 份候选，且并行（耗时≈单次而非 N×）。

用注入的假 generate（不碰真实 LLM）以确定性地验证并行与计数。
"""

from __future__ import annotations

import asyncio
import time

from app.nodes.fanout import fanout
from app.state import AgentSlot, Candidate, GroupState, Msg

DELAY = 0.2


async def _slow_gen(slot: AgentSlot, request: str) -> Candidate:
    await asyncio.sleep(DELAY)
    return Candidate(
        contact_id=slot.contact_id,
        dimension=slot.dimension,
        text=f"[{slot.contact_id}] {request}",
    )


async def test_fanout_parallel_and_count():
    n = 5
    roster = [AgentSlot(contact_id=f"a{i}", dimension=f"d{i}") for i in range(n)]
    state = GroupState(
        group_key="g",
        roster=roster,
        pending_human=Msg(sender_id="u", sender_kind="human", text="如何提高留存？"),
    )

    t0 = time.perf_counter()
    out = await fanout(state, generate=_slow_gen)
    elapsed = time.perf_counter() - t0

    cands = out["candidates"]
    assert len(cands) == n  # N 份候选
    assert {c.contact_id for c in cands} == {f"a{i}" for i in range(n)}
    assert all("如何提高留存" in c.text for c in cands)  # 需求传到了每个 agent
    assert elapsed < DELAY * 2  # 并行：总耗时 ≈ DELAY，而非 N*DELAY


async def test_fanout_reads_request_from_history_when_no_pending():
    roster = [AgentSlot(contact_id="a0")]
    state = GroupState(
        group_key="g",
        roster=roster,
        history=[Msg(sender_id="u", sender_kind="human", text="选题方向？")],
    )
    out = await fanout(state, generate=_slow_gen)
    assert out["candidates"][0].text == "[a0] 选题方向？"
