"""S4.4a 判据：RelayDriver 起圆桌 → 后台多轮 → 每轮 AI 发言经 OutboundClient 推回群。

注入假圆桌图（fake assign/gen/pick/extract + MemorySaver）+ 假 OutboundClient，离线确定性：
群发问 → 轮流 A/B/C 各被 speak 一次（N bot 轮流冒泡）；canonical_thread 去平台段。
"""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.recipes_roundtable import build_roundtable_recipe
from app.relay import RelayDriver, canonical_thread
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 说了一句")


async def _fake_extract(text, speaker_id, turn_idx) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


class _FakeOutbound:
    def __init__(self):
        self.calls = []

    async def speak(self, group_key, contact_id, text):
        self.calls.append((group_key, contact_id, text))
        return {"ok": True}


def _graph():
    return build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        pick=_round_robin(),
        human_in_loop=True,
    )


def test_canonical_thread_strips_platform():
    assert canonical_thread("ada1:GroupMessage:-519") == "GroupMessage:-519"
    assert canonical_thread("ada2:GroupMessage:-519") == "GroupMessage:-519"  # 多 bot 归一


async def test_inbound_starts_discussion_and_pushes_each_turn():
    out = _FakeOutbound()

    async def roster():
        return ["A", "B", "C"]

    driver = RelayDriver(_graph(), out, roster, max_turns=3)
    res = await driver.handle_inbound("ada1:GroupMessage:-519", "要不要做付费会员")
    assert res["status"] == "started"

    await driver._tasks["GroupMessage:-519"]  # 等后台讨论跑完

    # 三轮 AI 发言各被推回群一次，顺序 A→B→C，群标识保留原 umo（供桥换 bot_id）
    ids = [c[1] for c in out.calls]
    assert ids == ["A", "B", "C"]
    assert all(c[0] == "ada1:GroupMessage:-519" for c in out.calls)
    assert all("说了一句" in c[2] for c in out.calls)


async def test_no_roster_does_not_start():
    out = _FakeOutbound()

    async def empty_roster():
        return []

    driver = RelayDriver(_graph(), out, empty_roster)
    res = await driver.handle_inbound("ada1:GroupMessage:-519", "x")
    assert res["status"] == "no_roster"
    assert out.calls == []
