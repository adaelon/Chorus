"""S10b 判据：deliver 选择闸 + roundtable_deliver（§6.21）——结束才定要结论还是产出。

human_gate 的 end 不直奔 synthesize，先过 deliver（问人"结论/产出"）→ 路由到 synthesize/produce。
deliver 纯选择闸（只写 next_decision、不碰 output），复用两主笔。注入假节点依赖，离线。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.schedule import NextSpeaker
from app.recipes.builtin import ROUNDTABLE_DELIVER
from app.recipes.compile import compile_recipe
from app.recipes.validate import validate_recipe
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot, request, history, claims=None):
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 说")


async def _fake_extract(text, speaker_id, turn_idx):
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}点", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


async def _fake_compose(state: GroupState) -> str:  # synthesize（出结论）
    return "DECIDE-OUT"


async def _fake_produce(state: GroupState) -> str:  # produce（出产物）
    return "PRODUCE-OUT"


def test_roundtable_deliver_validates():
    assert validate_recipe(ROUNDTABLE_DELIVER) == []


def _graph():
    deps = {
        "assign": _fake_assign,
        "generate": _fake_gen,
        "extract": _fake_extract,
        "pick": _round_robin(),
        "compose": _fake_compose,
        "compose_produce": _fake_produce,
    }
    return compile_recipe(ROUNDTABLE_DELIVER, MemorySaver(), deps=deps)


def _state_in(key):
    return {
        "group_key": key,
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B")],
        "history": [Msg(sender_id="u", sender_kind="human", text="帮我搞定这个任务")],
        "max_turns_per_human": 5,
    }


async def _to_deliver(graph, key):
    """起场 → A 发言停 human_gate → 人 end → 停在 deliver 选择闸。"""
    cfg = {"configurable": {"thread_id": key}}
    out = await graph.ainvoke(_state_in(key), cfg)
    assert out["__interrupt__"][0].value["type"] == "human_gate"
    out2 = await graph.ainvoke(Command(resume={"end": True}), cfg)  # end → deliver（不直奔 synthesize）
    assert out2["__interrupt__"][0].value["type"] == "deliver"  # 选择闸暂停问人
    return cfg


async def test_choice_produce_runs_produce():
    graph = _graph()
    cfg = await _to_deliver(graph, "d_p")
    out = await graph.ainvoke(Command(resume={"choice": "produce"}), cfg)
    assert "__interrupt__" not in out
    assert out["output"] == "PRODUCE-OUT"  # 选产出 → produce 主笔


async def test_choice_decide_runs_synthesize():
    graph = _graph()
    cfg = await _to_deliver(graph, "d_d")
    out = await graph.ainvoke(Command(resume={"choice": "decide"}), cfg)
    assert "__interrupt__" not in out
    assert out["output"] == "DECIDE-OUT"  # 选结论 → synthesize 主笔


async def test_choice_default_is_decide():
    """缺省/非 produce 的 choice → 出结论（保守默认）。"""
    graph = _graph()
    cfg = await _to_deliver(graph, "d_x")
    out = await graph.ainvoke(Command(resume={"choice": "whatever"}), cfg)
    assert out["output"] == "DECIDE-OUT"
