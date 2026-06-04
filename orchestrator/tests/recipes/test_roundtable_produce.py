"""S10a 判据：roundtable_produce 配方（§6.21）——末端 produce，人结束 → 交付产物。

= ROUNDTABLE 末端 synthesize 换 produce；过 validate；端到端注入假 compose_produce，人 end →
produce 跑到 output（产物，非纪要）。注入假节点依赖，离线。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.schedule import NextSpeaker
from app.recipes.builtin import ROUNDTABLE_PRODUCE
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


async def _fake_produce(state: GroupState) -> str:
    return "PRODUCT: 一份可直接用的 prompt"


def test_roundtable_produce_validates():
    assert validate_recipe(ROUNDTABLE_PRODUCE) == []


def _graph():
    deps = {
        "assign": _fake_assign,
        "generate": _fake_gen,
        "extract": _fake_extract,
        "pick": _round_robin(),
        "compose_produce": _fake_produce,
    }
    return compile_recipe(ROUNDTABLE_PRODUCE, MemorySaver(), deps=deps)


async def test_end_runs_produce_not_synthesize():
    graph = _graph()
    cfg = {"configurable": {"thread_id": "p1"}}
    state_in = {
        "group_key": "p1",
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B")],
        "history": [Msg(sender_id="u", sender_kind="human", text="帮我写个 prompt")],
        "max_turns_per_human": 5,
    }
    out = await graph.ainvoke(state_in, cfg)  # A 发言后停在 human_gate
    assert out["__interrupt__"][0].value["type"] == "human_gate"

    out2 = await graph.ainvoke(Command(resume={"end": True}), cfg)  # 人结束 → produce
    assert "__interrupt__" not in out2
    assert out2["output"] == "PRODUCT: 一份可直接用的 prompt"  # 交付产物（经 compose_produce）
