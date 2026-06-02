"""S5.2 判据：auto 配方 L3 主持人组原语——按假 planner 出合法序列 + 步数闸必停（§B2）。

注入假 assign/gen/extract + 假 planner（控制原语序列）+ MemorySaver，离线确定性。
验：planner 出 [Fanout, Speak A, Speak B, Synthesize] → 引擎依次 dispatch（候选/发言/产出）；
planner 恒 Speak → 步数闸 max_plan_steps 到顶强制 synthesize→END（不无限循环）。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.nodes.plan import Fanout, Speak, Synthesize
from app.recipes_auto import build_auto_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的内容")


async def _fake_extract(text, speaker_id, turn_idx) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _seq_planner(decisions):
    """按预设序列逐次返回原语；用尽后 Synthesize 收尾。"""
    it = iter(decisions)

    async def planner(state: GroupState):
        return next(it, Synthesize())

    return planner


def _always_speak(cid):
    async def planner(state: GroupState):
        return Speak(contact_id=cid)

    return planner


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _state_in(key, **kw):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B")],
        "history": [Msg(sender_id="u", sender_kind="human", text="任务X")],
        **kw,
    }


def _graph(planner):
    return build_auto_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        planner=planner,
    )


async def test_auto_dispatches_planned_primitive_sequence():
    planner = _seq_planner([Fanout(), Speak(contact_id="A"), Speak(contact_id="B"), Synthesize()])
    graph = _graph(planner)
    out = await graph.ainvoke(_state_in("a1"), _cfg("a1"))

    assert "__interrupt__" not in out  # 跑到 END
    # Fanout 跑过 → 有候选
    assert out["candidates"] and {c.contact_id for c in out["candidates"]} == {"A", "B"}
    # 两次 Speak 依次发言（A 后 B）
    ai = [m for m in out["history"] if m.sender_kind == "ai"]
    assert [m.sender_id for m in ai] == ["A", "B"]
    # Synthesize 出产出
    assert out["output"]


async def test_auto_step_gate_forces_stop():
    graph = _graph(_always_speak("A"))
    out = await graph.ainvoke(_state_in("a2", max_plan_steps=3), _cfg("a2"))

    assert "__interrupt__" not in out  # 步数闸到顶 → synthesize → END，不无限循环
    ai = [m for m in out["history"] if m.sender_kind == "ai"]
    assert len(ai) == 3  # 恰好 max_plan_steps 次 Speak
    assert out["stop_reason"] == "plan_budget"
    assert out["output"]
