"""S9a 判据：圆桌 @定向插话（§6.20）——@某人=只让他改、不@=对全员。

人在环圆桌每轮停在 human_gate。resume 带 `directed`（前端 chips 选 → contact_id 列表）→
schedule 按序只让这几位修改（跳过主持人挑人与预算闸），批量跑完才停回到人、主持人不接力；
不带 directed → 维持现状（主持人挑谁接）。注入假节点依赖，离线。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.schedule import NextSpeaker
from app.recipes.roundtable import build_roundtable_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _counting_pick():
    """round-robin 主持人，但记录每次被调用——用于断言"定向轮不调主持人"。"""
    calls: list[list[str]] = []

    async def pick(state: GroupState) -> NextSpeaker:
        calls.append([s.contact_id for s in state.roster])
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    pick.calls = calls  # type: ignore[attr-defined]
    return pick


def _graph(pick=None, captured: list | None = None):
    async def gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
        if captured is not None:
            captured.append((slot.contact_id, request))
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的发言")

    return build_roundtable_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=gen,
        extract=_fake_extract,
        pick=pick or _counting_pick(),
        human_in_loop=True,
    )


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _state_in(key: str, max_turns: int = 6):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C", "D")],
        "history": [Msg(sender_id="u", sender_kind="human", text="圆桌议题")],
        "max_turns_per_human": max_turns,
    }


def _ai(snap):
    return [m for m in snap.values["history"] if m.sender_kind == "ai"]


async def test_directed_single_only_them_then_back_to_human():
    """@单人 → 只他发言 → 停回 human_gate；主持人不接力（不被调用）。"""
    pick = _counting_pick()
    graph = _graph(pick=pick)
    cfg = _cfg("d1")
    await graph.ainvoke(_state_in("d1"), cfg)  # 起场：A 发言（主持人挑），停 human_gate
    n_before = len(pick.calls)

    out = await graph.ainvoke(Command(resume={"interject": "把方案改省点钱", "directed": ["B"]}), cfg)

    # 停回 human_gate（B 说完即停，主持人没有自动接力让别人说）
    assert out["__interrupt__"][0].value["type"] == "human_gate"
    assert len(pick.calls) == n_before  # 定向轮跳过主持人
    snap = await graph.aget_state(cfg)
    assert _ai(snap)[-1].sender_id == "B"  # 最后发言是被 @ 的 B
    assert any(m.sender_kind == "human" and "改省点钱" in m.text for m in snap.values["history"])
    assert snap.values["directed_queue"] == []  # 队列排空


async def test_directed_multiple_speak_in_order():
    """@多人 → 按 @ 顺序全发完才停（批量不连锁）。"""
    pick = _counting_pick()
    graph = _graph(pick=pick)
    cfg = _cfg("d2")
    await graph.ainvoke(_state_in("d2"), cfg)
    n_before = len(pick.calls)

    out = await graph.ainvoke(Command(resume={"interject": "改", "directed": ["C", "B"]}), cfg)

    assert out["__interrupt__"][0].value["type"] == "human_gate"
    assert len(pick.calls) == n_before  # 整批定向都不调主持人
    snap = await graph.aget_state(cfg)
    spoke = [m.sender_id for m in _ai(snap)]
    assert spoke[-2:] == ["C", "B"]  # 按 @ 顺序
    assert snap.values["directed_queue"] == []


async def test_no_directed_keeps_moderator_pick():
    """不@ → 维持现状：主持人挑谁接（directed_queue 恒空）。"""
    pick = _counting_pick()
    graph = _graph(pick=pick)
    cfg = _cfg("d3")
    await graph.ainvoke(_state_in("d3"), cfg)
    n_before = len(pick.calls)

    await graph.ainvoke(Command(resume={"interject": "换个方向：聚焦现金流"}), cfg)

    assert len(pick.calls) > n_before  # 主持人被调用挑人
    snap = await graph.aget_state(cfg)
    assert snap.values.get("directed_queue", []) == []  # 从未写入 → 缺省空


async def test_directed_skips_budget_gate():
    """定向跳过预算闸：@ 三人即便轮数超 max 也全发完（人明确指派）。"""
    graph = _graph()
    cfg = _cfg("d4")
    await graph.ainvoke(_state_in("d4", max_turns=2), cfg)  # A 发言(turns=1)，停

    # @[B,C,D]：human_gate 文本重置 turns=0 → B(1) C(2) D(3)；C 后 turns=2>=max=2
    # 若不跳闸，schedule 会先触预算停、D 永不发言 → 断言 D 发言即证跳闸。
    out = await graph.ainvoke(Command(resume={"interject": "都改", "directed": ["B", "C", "D"]}), cfg)

    assert out["__interrupt__"][0].value["type"] == "human_gate"
    snap = await graph.aget_state(cfg)
    assert [m.sender_id for m in _ai(snap)][-3:] == ["B", "C", "D"]
    assert snap.values["turns_since_human"] == 3  # 超过 max=2，证未被预算闸截停


async def test_directed_turn_prompt_framed_others_not():
    """turn 定向时 prompt 框架为"真人点名要你修改"；非定向轮无此框架。"""
    captured: list[tuple[str, str]] = []
    graph = _graph(captured=captured)
    cfg = _cfg("d5")
    await graph.ainvoke(_state_in("d5"), cfg)  # A 非定向发言

    await graph.ainvoke(Command(resume={"interject": "改成更激进", "directed": ["B"]}), cfg)

    b_req = [r for cid, r in captured if cid == "B"][-1]
    assert "真人点名" in b_req and "改成更激进" in b_req
    a_req = [r for cid, r in captured if cid == "A"][0]
    assert "真人点名" not in a_req  # 起场非定向轮无框架
