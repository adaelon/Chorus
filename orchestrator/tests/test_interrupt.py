"""S3.0 判据：扇出整图的 interrupt/resume 往返 + 多轮 curate（模型 A，§6.10）。

直接驱动 `build_fanout_recipe` 编译出的图（注入假 assign/generate + MemorySaver），
断言：① FANOUT 后停在 CURATE 的 interrupt，payload 带候选；② resume(curate) apply 后
回到 interrupt，状态累积；③ 第二轮 curate 仍是 interrupt（多轮循环）；④ resume(synthesize)
跑到 END 产出 output。全程离线、确定性。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.recipes import build_fanout_recipe
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(
        contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}] {request}"
    )


def _graph():
    return build_fanout_recipe(MemorySaver(), assign=_fake_assign, generate=_fake_gen)


def _cfg(k):
    return {"configurable": {"thread_id": k}}


async def test_fanout_stops_at_curate_interrupt():
    graph = _graph()
    cfg = _cfg("g")
    result = await graph.ainvoke(
        {
            "group_key": "g",
            "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C")],
            "pending_human": {"sender_id": "u", "sender_kind": "human", "text": "便利店选题"},
        },
        cfg,
    )
    # 图暂停在 CURATE 的 interrupt（未跑到 END，无 output）
    intr = result["__interrupt__"]
    payload = intr[0].value
    assert payload["type"] == "curate"
    assert {c["contact_id"] for c in payload["candidates"]} == {"A", "B", "C"}
    assert result.get("output") is None


async def test_curate_resume_roundtrip_then_multi_round_then_synthesize():
    graph = _graph()
    cfg = _cfg("g")
    await graph.ainvoke(
        {
            "group_key": "g",
            "roster": [AgentSlot(contact_id=c) for c in ("A", "B", "C")],
            "pending_human": {"sender_id": "u", "sender_kind": "human", "text": "便利店选题"},
        },
        cfg,
    )

    # 第 1 轮 curate：eliminate A + pick B + reassign 给 C → 回到 interrupt
    r1 = await graph.ainvoke(
        Command(
            resume={
                "action": "curate",
                "commands": [
                    {"kind": "eliminate", "contact_id": "A"},
                    {"kind": "pick", "contact_id": "B", "point": "B 的要点"},
                    {"kind": "reassign", "point": "A 的现金流视角", "executor_id": "C"},
                ],
            }
        ),
        cfg,
    )
    p1 = r1["__interrupt__"][0].value  # 多轮：仍停在 curate
    ids = {c["contact_id"] for c in p1["candidates"]}
    assert "A" not in ids  # eliminate 生效
    assert any("A 的现金流视角" in c["text"] for c in p1["candidates"] if c["contact_id"] == "C")
    assert any(c["text"] == "B 的要点" for c in p1["picked"])

    # 第 2 轮 curate：再 pick C → 仍回到 interrupt，picked 累积
    r2 = await graph.ainvoke(
        Command(resume={"action": "curate", "commands": [{"kind": "pick", "contact_id": "C"}]}),
        cfg,
    )
    p2 = r2["__interrupt__"][0].value
    picked_ids = [c["contact_id"] for c in p2["picked"]]
    assert "B" in picked_ids and "C" in picked_ids  # 两轮累积

    # resume(synthesize)：跑到 END，产出含 picked
    r3 = await graph.ainvoke(Command(resume={"action": "synthesize"}), cfg)
    assert "__interrupt__" not in r3
    assert "B 的要点" in r3["output"]
