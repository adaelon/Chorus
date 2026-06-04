"""S9b 判据：@定向修订偏最新 + 旧主张去重（§6.20/§6.11）。

被@者按指令改了立场 → 追加一条新发言（history append-only），但点账本（合成/远场/主持人
都读它）只留该人最新一版、旧点去重。非定向重复发言仍累积（§6.11 不变）。注入假节点，离线。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.schedule import NextSpeaker
from app.recipes.roundtable import build_roundtable_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


def _versioned_gen():
    """每次发言产出唯一版本文本（contact#N），便于区分旧版/新版。"""
    n = {"c": 0}

    async def gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
        n["c"] += 1
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id}#{n['c']}")

    return gen


async def _text_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=text, turn=turn_idx)]  # 点 = 发言文本


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _always(cid: str):
    async def pick(state: GroupState) -> NextSpeaker:
        return NextSpeaker(contact_id=cid)

    return pick


def _graph(pick):
    return build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=_versioned_gen(),
        extract=_text_extract,
        pick=pick,
        human_in_loop=True,
    )


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _state_in(key: str):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C", "D")],
        "history": [Msg(sender_id="u", sender_kind="human", text="圆桌议题")],
        "max_turns_per_human": 6,
    }


def _claims_of(snap, sid):
    return [c.text for c in snap.values["claims"] if c.speaker_id == sid]


async def test_directed_revision_supersedes_speaker_claims():
    """@A 修订 → A 点账本只剩最新一版（旧点去重），history 两版都留。"""
    graph = _graph(_round_robin())
    cfg = _cfg("b1")
    await graph.ainvoke(_state_in("b1"), cfg)  # 起场：A 发言 A#1，点 A→"A#1"
    snap0 = await graph.aget_state(cfg)
    assert _claims_of(snap0, "A") == ["A#1"]

    # @A 修订 → A 再发言 A#2
    await graph.ainvoke(Command(resume={"interject": "把你的方案改激进点", "directed": ["A"]}), cfg)
    snap = await graph.aget_state(cfg)

    assert _claims_of(snap, "A") == ["A#2"]  # 旧点 A#1 去重，只剩最新
    a_speech = [m.text for m in snap.values["history"] if m.sender_id == "A"]
    assert a_speech == ["A#1", "A#2"]  # history append-only：两版原文都在


async def test_synthesize_uses_revised_not_stale():
    """修订后收尾合成（确定性兜底）用新版、不双算旧主张。"""
    graph = _graph(_round_robin())
    cfg = _cfg("b2")
    await graph.ainvoke(_state_in("b2"), cfg)  # A#1
    await graph.ainvoke(Command(resume={"interject": "改", "directed": ["A"]}), cfg)  # A#2 替换 A#1

    out = await graph.ainvoke(Command(resume={"end": True}), cfg)  # 人收尾 → fallback 合成
    assert out["output"]
    assert "A#2" in out["output"] and "A#1" not in out["output"]  # 偏最新、不双算旧


async def test_non_directed_repeat_accumulates_claims():
    """非定向重复发言仍累积（§6.11 不变）——supersede 仅对定向修订生效。"""
    graph = _graph(_always("A"))
    cfg = _cfg("b3")
    await graph.ainvoke(_state_in("b3"), cfg)  # A#1
    await graph.ainvoke(Command(resume={"interject": None}), cfg)  # 继续（非定向）→ A 又发言 A#2

    snap = await graph.aget_state(cfg)
    assert _claims_of(snap, "A") == ["A#1", "A#2"]  # 累积不去重
