"""S4.4a 判据：RelayDriver 起圆桌 → 后台多轮 → 每轮 AI 发言经 OutboundClient 推回群。

注入假圆桌图（fake assign/gen/pick/extract + MemorySaver）+ 假 OutboundClient，离线确定性：
群发问 → 轮流 A/B/C 各被 speak 一次（N bot 轮流冒泡）；canonical_thread 去平台段。
"""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.recipes.roundtable import build_roundtable_recipe
from app.transport.relay import RelayDriver, canonical_thread
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


async def test_next_resume_consumes_interjection_queue():
    """S4.4b：队列有人类插话 → resume 带 interject；空 → 继续。"""
    out = _FakeOutbound()

    async def roster():
        return ["A"]

    driver = RelayDriver(_graph(), out, roster)
    driver._queues["t1"] = asyncio.Queue()
    assert driver._next_resume("t1") == {"interject": None}  # 空队列：继续
    driver._queues["t1"].put_nowait("换个方向：聚焦现金流")
    assert driver._next_resume("t1") == {"interject": "换个方向：聚焦现金流"}  # 消费插话
    assert driver._next_resume("t1") == {"interject": None}  # 已取走


async def test_inbound_while_running_enqueues_interjection():
    """S4.4b：讨论进行中再来人类消息 → 入队（不起新场），status=interjected。"""
    out = _FakeOutbound()

    async def roster():
        return ["A"]

    driver = RelayDriver(_graph(), out, roster)
    thread = canonical_thread("ada1:GroupMessage:-9")
    driver._queues[thread] = asyncio.Queue()
    driver._tasks[thread] = asyncio.create_task(asyncio.sleep(5))  # 模拟进行中
    try:
        res = await driver.handle_inbound("ada1:GroupMessage:-9", "插一句：预算有限")
        assert res["status"] == "interjected"
        assert driver._queues[thread].get_nowait() == "插一句：预算有限"
    finally:
        driver._tasks[thread].cancel()


async def test_interjection_redirects_and_enters_history():
    """S4.4b 端到端：预置插话 → 讨论改向（插话进 history、预算归零后继续多发言）。"""
    out = _FakeOutbound()

    async def roster():
        return ["A", "B"]

    driver = RelayDriver(_graph(), out, roster, max_turns=2)
    thread = canonical_thread("ada1:GroupMessage:-7")
    # 先建好队列并预置一条插话，再手动跑 _run（绕过后台 task 的时序）
    driver._queues[thread] = asyncio.Queue()
    driver._queues[thread].put_nowait("请聚焦现金流")
    await driver._run(thread, "ada1:GroupMessage:-7", ["A", "B"], "要不要做付费会员")

    snap = await driver._graph.aget_state({"configurable": {"thread_id": thread}})
    hist = snap.values["history"]
    # 插话被消费、进了群历史（改向）
    assert any(m.sender_kind == "human" and "现金流" in m.text for m in hist)
    # 预算归零后又继续发言 → AI 发言数 > 初始预算 2（证明改向延续了讨论）
    ai = [m for m in hist if m.sender_kind == "ai"]
    assert len(ai) > 2


async def test_empty_turn_not_pushed():
    """防御：某轮模型只出 reasoning（text 空）→ 不推空消息到群（telegram 拒空）。"""

    async def gen_b_empty(slot, request, history, claims=None):
        text = "" if slot.contact_id == "B" else f"{slot.contact_id} 有话说"
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=text)

    graph = build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=gen_b_empty,
        extract=_fake_extract,
        pick=_round_robin(),
        human_in_loop=True,
    )
    out = _FakeOutbound()

    async def roster():
        return ["A", "B", "C"]

    driver = RelayDriver(graph, out, roster, max_turns=3)
    await driver._run("t", "ada1:GroupMessage:-5", ["A", "B", "C"], "议题")

    ids = [c[1] for c in out.calls]
    assert ids == ["A", "C"]  # B 空 → 跳过


async def test_no_roster_does_not_start():
    out = _FakeOutbound()

    async def empty_roster():
        return []

    driver = RelayDriver(_graph(), out, empty_roster)
    res = await driver.handle_inbound("ada1:GroupMessage:-519", "x")
    assert res["status"] == "no_roster"
    assert out.calls == []
