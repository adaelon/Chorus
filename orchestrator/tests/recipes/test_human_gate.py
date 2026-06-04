"""S3.4 判据：圆桌讨论中注入人类消息 → 下一步让位/改向（断言状态转移）。

human_in_loop=True 时每轮发言后停在 human_gate interrupt（让位窗口，复用 S3.0 机制）。
两条注入通道：① resume 带 interject；② 外部 aupdate_state 写 pending_human。
任一有人类输入 → 消息进 history + 预算闸归零（改向）。注入假节点依赖，离线。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.schedule import NextSpeaker, Stop
from app.recipes.roundtable import build_roundtable_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的发言")


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin():
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
        pick=_round_robin(),
        human_in_loop=True,
    )


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _state_in(key: str):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C")],
        "history": [Msg(sender_id="u", sender_kind="human", text="圆桌议题")],
        "max_turns_per_human": 5,
    }


async def test_pauses_at_human_gate_after_each_turn():
    graph = _graph()
    cfg = _cfg("h1")
    out = await graph.ainvoke(_state_in("h1"), cfg)
    # 第一轮 A 发言后停在 human_gate（让位窗口），未跑到预算闸 END
    assert out["__interrupt__"][0].value["type"] == "human_gate"
    snap = await graph.aget_state(cfg)
    assert snap.values["turns_since_human"] == 1  # 仅 A 发言一轮


async def test_interject_redirects_discussion():
    graph = _graph()
    cfg = _cfg("h2")
    await graph.ainvoke(_state_in("h2"), cfg)  # 停在第一轮 human_gate

    # 讨论中注入人类插话（resume 通道）→ 让位后改向
    await graph.ainvoke(Command(resume={"interject": "换个方向：聚焦现金流"}), cfg)
    snap = await graph.aget_state(cfg)
    hist = snap.values["history"]

    # 人类消息进了 history（状态转移）
    assert any(m.sender_kind == "human" and "聚焦现金流" in m.text for m in hist)
    # 改向：注入后预算闸归零、随后又发言一轮 → turns==1
    assert snap.values["turns_since_human"] == 1
    assert snap.values["pending_human"] is None


async def test_pending_human_async_injection_consumed():
    graph = _graph()
    cfg = _cfg("h3")
    await graph.ainvoke(_state_in("h3"), cfg)  # 停在第一轮 human_gate

    # 异步注入通道：外部写 pending_human（service 真实通道，S3.6 wire）
    await graph.aupdate_state(
        cfg, {"pending_human": Msg(sender_id="u", sender_kind="human", text="插一句：预算有限")}
    )
    await graph.ainvoke(Command(resume={"interject": None}), cfg)  # 不当场 interject，但 pending_human 待消化
    snap = await graph.aget_state(cfg)
    hist = snap.values["history"]

    assert any(m.sender_kind == "human" and "预算有限" in m.text for m in hist)  # 注入被纳入
    assert snap.values["pending_human"] is None  # 已消化（防再次 yield 死循环）
    assert snap.values["turns_since_human"] == 1  # 改向重置后又一轮


async def test_end_routes_to_synthesize():
    """S3.6h：human_gate resume {"end": true} → 直接主笔综合到 END（不靠预算闸/主持人）。"""
    graph = _graph()
    cfg = _cfg("h5")
    await graph.ainvoke(_state_in("h5"), cfg)  # A 发言后停在 human_gate（turns=1，远未到预算闸 5）

    out = await graph.ainvoke(Command(resume={"end": True}), cfg)
    # 跑到 END（非再次 interrupt），主笔综合产出非空（点账本有 A 的点）
    assert "__interrupt__" not in out
    assert out["output"]
    assert out["turns_since_human"] == 1  # 未到预算闸，是手动收尾


async def test_moderator_stop_asks_human_not_synthesize():
    """S8a/§6.19：主持人建议结束 → 停在 human_gate（人拍板），不直接收尾。"""

    def _speak_then_stop():
        n = {"c": 0}

        async def pick(state: GroupState):
            n["c"] += 1
            if n["c"] == 1:
                return NextSpeaker(contact_id="A")
            return Stop(reason="moderator")  # 之后主持人建议结束

        return pick

    graph = build_roundtable_recipe(
        MemorySaver(), assign=_fake_assign, generate=_fake_gen, extract=_fake_extract,
        pick=_speak_then_stop(), human_in_loop=True,
    )
    cfg = _cfg("h6")
    await graph.ainvoke(_state_in("h6"), cfg)  # A 发言 → post-turn human_gate
    # 继续 → 主持人 stop(moderator) → 停在 human_gate（reason=moderator），未擅自收尾
    out = await graph.ainvoke(Command(resume={"interject": None}), cfg)
    assert out["__interrupt__"][0].value["type"] == "human_gate"
    assert out["__interrupt__"][0].value["reason"] == "moderator"
    assert not (await graph.aget_state(cfg)).values.get("output")

    # 人点结束才真收尾（A 已发言，点账本有内容 → 合成非空）
    out2 = await graph.ainvoke(Command(resume={"end": True}), cfg)
    assert "__interrupt__" not in out2 and out2["output"]


async def test_budget_yields_to_human_and_resets_on_continue():
    """S8a/§6.19：预算闸触顶 → 让位给人（非结束）；人续→turns 归零、不立刻再触顶。"""
    graph = build_roundtable_recipe(
        MemorySaver(), assign=_fake_assign, generate=_fake_gen, extract=_fake_extract,
        pick=_round_robin(), human_in_loop=True,
    )
    cfg = _cfg("h7")
    # max=1：A 发言一轮(turns=1)→post-turn human_gate(reason=None)
    state_in = {**_state_in("h7"), "max_turns_per_human": 1}
    out = await graph.ainvoke(state_in, cfg)
    assert out["__interrupt__"][0].value["reason"] is None  # 普通每轮停

    # 继续 → schedule 预算触顶 → 让位给人(reason=budget)，不收尾
    out2 = await graph.ainvoke(Command(resume={"interject": None}), cfg)
    assert out2["__interrupt__"][0].value["type"] == "human_gate"
    assert out2["__interrupt__"][0].value["reason"] == "budget"
    assert not (await graph.aget_state(cfg)).values.get("output")

    # 再继续 → 预算归零、又能发言一轮（没死循环）
    await graph.ainvoke(Command(resume={"interject": None}), cfg)
    snap = await graph.aget_state(cfg)
    assert snap.values["turns_since_human"] == 1  # 归零后又一轮
    assert snap.values["stop_reason"] is None


async def test_no_interject_continues_discussion():
    graph = _graph()
    cfg = _cfg("h4")
    await graph.ainvoke(_state_in("h4"), cfg)  # A 发言后停

    await graph.ainvoke(Command(resume={"interject": None}), cfg)  # 不插话，继续
    snap = await graph.aget_state(cfg)
    # 没有人类插话 → 预算照常累积（A、B 两轮），讨论继续
    assert snap.values["turns_since_human"] == 2
    ai = [m for m in snap.values["history"] if m.sender_kind == "ai"]
    assert len(ai) == 2 and not any(m.sender_kind == "human" and "插" in m.text for m in snap.values["history"])
