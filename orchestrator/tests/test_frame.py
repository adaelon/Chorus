"""S1.4 判据：FRAME 给 roster 每个 slot 分到非空 dimension；CLARIFY 直通。"""

from __future__ import annotations

import os

import pytest

from app.nodes.clarify import clarify
from app.nodes.frame import frame
from app.state import AgentSlot, GroupState, Msg


async def test_frame_assigns_dimension_to_each_slot():
    roster = [AgentSlot(contact_id="老陈"), AgentSlot(contact_id="小杨"), AgentSlot(contact_id="阿May")]
    state = GroupState(
        group_key="g",
        roster=roster,
        pending_human=Msg(sender_id="u", sender_kind="human", text="便利店要不要关店"),
    )

    async def fake_assign(request, roster):
        return {s.contact_id: f"维度-{s.contact_id}" for s in roster}

    out = await frame(state, assign=fake_assign)
    new_roster = out["roster"]

    assert len(new_roster) == 3
    assert all(s.dimension for s in new_roster)  # 每个 slot 拿到非空维度
    assert {s.contact_id for s in new_roster} == {"老陈", "小杨", "阿May"}


async def test_clarify_passthrough():
    out = await clarify(GroupState(group_key="g"))
    assert out == {}  # 占位：不改 state，直通


@pytest.mark.skipif(
    not os.environ.get("CHORUS_RUN_SMOKE"),
    reason="设置 CHORUS_RUN_SMOKE=1 跑真实主持人(结构化输出)",
)
async def test_frame_smoke_real_moderator():
    roster = [AgentSlot(contact_id="a"), AgentSlot(contact_id="b")]
    state = GroupState(
        group_key="g",
        roster=roster,
        pending_human=Msg(sender_id="u", sender_kind="human", text="如何提高新用户次日留存"),
    )
    out = await frame(state)  # 真实 LLM 结构化输出
    new_roster = out["roster"]
    assert all(s.dimension for s in new_roster)
