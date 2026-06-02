"""S3.3 判据：圆桌配方端到端跑通——CLARIFY→FRAME→(SCHEDULE⇄TURN)*→SYNTHESIZE。

全程注入假 assign/generate/extract/pick + MemorySaver，离线确定性。验证：
轮流发言到预算闸停 → 到 END；history 累积 N 条 ai 发言；点账本随轮累积；预算停止。
（git diff 未动 nodes/引擎 = §6.6 抽象成立，由提交保证。）
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.recipes_roundtable import build_roundtable_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(
        contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的发言"
    )


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin_pick():
    """轮流选下一发言人（靠预算闸停止）。"""

    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _graph():
    return build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        pick=_round_robin_pick(),
    )


def _cfg(k):
    return {"configurable": {"thread_id": k}}


async def test_roundtable_runs_to_end_until_budget():
    graph = _graph()
    cfg = _cfg("rt1")
    state_in = {
        "group_key": "rt1",
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C")],
        # 初始 request 作为开场 human 消息进 history；pending_human 留空（入口约定）
        "history": [Msg(sender_id="u", sender_kind="human", text="圆桌议题：要不要做付费会员")],
        "max_turns_per_human": 3,
    }
    out = await graph.ainvoke(state_in, cfg)

    # 跑到 END（非 interrupt）：决策最终为预算闸 Stop
    assert "__interrupt__" not in out
    assert out["next_decision"] == "stop" and out["stop_reason"] == "budget"

    # 恰好 3 轮 ai 发言（预算闸）
    assert out["turns_since_human"] == 3
    ai_msgs = [m for m in out["history"] if m.sender_kind == "ai"]
    assert len(ai_msgs) == 3
    assert [m.sender_id for m in ai_msgs] == ["A", "B", "C"]  # 轮流

    # 点账本随每轮累积、带归属
    assert len(out["claims"]) == 3
    assert {c.speaker_id for c in out["claims"]} == {"A", "B", "C"}

    # FRAME 给 roster 分了维度
    assert all(s.dimension for s in out["roster"])

    # SYNTHESIZE 圆桌变体（S3.6b）：从点账本主笔综合，不再是空串（补 S3.3 遗留）
    assert out["output"]
    assert all(sid in out["output"] for sid in ("A", "B", "C"))


async def test_roundtable_frame_runs_before_speaking():
    """第一个发言者看得到自己被分配的维度（FRAME 先于 TURN）。"""
    seen = {}

    async def capturing_gen(slot, request, history, claims=None):
        seen[slot.contact_id] = slot.dimension
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text="x")

    graph = build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=capturing_gen,
        extract=_fake_extract,
        pick=_round_robin_pick(),
    )
    await graph.ainvoke(
        {
            "group_key": "rt2",
            "roster": [AgentSlot(contact_id="A")],
            "history": [Msg(sender_id="u", sender_kind="human", text="议题")],
            "max_turns_per_human": 1,
        },
        _cfg("rt2"),
    )
    assert seen["A"] == "维度-A"  # 发言时已带 FRAME 分配的维度
