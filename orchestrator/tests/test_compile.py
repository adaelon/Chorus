"""S5.4.1b 判据：compile_recipe 直译声明式 JSON → 可跑的 StateGraph（§6.16 C）。

注入假节点依赖，离线确定性。验：节点绑定 + 普通边 + 条件边（next_decision）+ 通用 when
（读任意 state 字段）+ 条件边直达 END + 未注册原语报错。
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.recipes_compile import compile_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot, request, history, claims=None):
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 发言")


async def _fake_extract(text, speaker_id, turn):
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn)]


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _deps(**over):
    d = dict(assign=_fake_assign, generate=_fake_gen, extract=_fake_extract, pick=_round_robin(), compose=None)
    d.update(over)
    return d


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _state(key, **kw):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B")],
        "history": [Msg(sender_id="u", sender_kind="human", text="议题")],
        **kw,
    }


# 圆桌形状（无 clarify/human_gate）：靠 schedule 预算闸停 → 条件边 stop→synthesize。
_ROUNDTABLE = {
    "recipe": "rt-min", "version": 1,
    "nodes": [
        {"id": "frame", "use": "frame"},
        {"id": "schedule", "use": "schedule"},
        {"id": "turn", "use": "turn"},
        {"id": "synthesize", "use": "synthesize"},
    ],
    "edges": [
        {"from": "START", "to": "frame"},
        {"from": "frame", "to": "schedule"},
        {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
        {"from": "schedule", "to": "synthesize"},  # else
        {"from": "turn", "to": "schedule"},
        {"from": "synthesize", "to": "END"},
    ],
}


async def test_compiles_and_runs_roundtable_shape():
    graph = compile_recipe(_ROUNDTABLE, MemorySaver(), deps=_deps())
    out = await graph.ainvoke(_state("c1", max_turns_per_human=2), _cfg("c1"))
    assert "__interrupt__" not in out  # 跑到 END
    ai = [m for m in out["history"] if m.sender_kind == "ai"]
    assert len(ai) == 2  # 预算闸 max=2 → 两轮后 schedule stop → synthesize
    assert out["output"]  # 兜底主笔（claims 归并）非空
    assert out["stop_reason"] == "budget"


async def test_general_when_routes_on_state_field():
    """通用 when：边读任意 state 字段（turns_since_human），不限 next_decision。"""
    recipe = {
        "recipe": "when-min", "version": 1,
        "nodes": [
            {"id": "frame", "use": "frame"},
            {"id": "schedule", "use": "schedule"},
            {"id": "turn", "use": "turn"},
            {"id": "synthesize", "use": "synthesize"},
        ],
        "edges": [
            {"from": "START", "to": "frame"},
            {"from": "frame", "to": "schedule"},
            {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
            {"from": "schedule", "to": "synthesize"},
            # 通用 when：发言到 2 轮就收尾，否则回 schedule（永不靠 schedule 自身预算闸）
            {"from": "turn", "when": {"field": "turns_since_human", "op": ">=", "value": 2}, "to": "synthesize"},
            {"from": "turn", "to": "schedule"},
            {"from": "synthesize", "to": "END"},
        ],
    }
    graph = compile_recipe(recipe, MemorySaver(), deps=_deps())
    out = await graph.ainvoke(_state("c2", max_turns_per_human=99), _cfg("c2"))
    assert "__interrupt__" not in out
    ai = [m for m in out["history"] if m.sender_kind == "ai"]
    assert len(ai) == 2  # 由 turns_since_human>=2 这条通用 when 收尾，非 schedule 预算闸
    assert out["output"]


async def test_conditional_branch_to_end():
    """条件边可直达 END（path_map 里 "END"→常量）。"""
    recipe = {
        "recipe": "end-min", "version": 1,
        "nodes": [
            {"id": "frame", "use": "frame"},
            {"id": "schedule", "use": "schedule"},
            {"id": "turn", "use": "turn"},
        ],
        "edges": [
            {"from": "START", "to": "frame"},
            {"from": "frame", "to": "schedule"},
            {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
            {"from": "schedule", "to": "END"},  # else 直达 END（不收尾）
            {"from": "turn", "to": "schedule"},
        ],
    }
    graph = compile_recipe(recipe, MemorySaver(), deps=_deps())
    out = await graph.ainvoke(_state("c3", max_turns_per_human=1), _cfg("c3"))
    assert "__interrupt__" not in out  # 一轮后预算闸 stop → 条件边直达 END
    assert len([m for m in out["history"] if m.sender_kind == "ai"]) == 1


def test_unknown_primitive_raises():
    bad = {"recipe": "x", "version": 1, "nodes": [{"id": "n", "use": "nope"}], "edges": []}
    with pytest.raises(ValueError, match="未注册原语"):
        compile_recipe(bad, MemorySaver(), deps={})
