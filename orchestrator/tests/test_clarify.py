"""S3.5 判据：模糊需求触发澄清问、清晰需求直通、"跳过"强制进 FRAME（§6.5 档位 B）。

通过扇出配方驱动（interrupt 需编译图）：注入假 assess/assign/generate + MemorySaver。
clarify 信心不足→停在 type=clarify 的 interrupt；信心足→直通到 curate；
resume skip→强制进 FRAME（到 curate）；resume answer→答复并入 history。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.nodes.clarify import ClarifyAssessment
from app.recipes import build_fanout_recipe
from app.state import AgentSlot, Candidate, Msg


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}]")


def _assess_by_keyword():
    """含"模糊"→信心低（要澄清），否则高（直通）。"""

    async def assess(request: str) -> ClarifyAssessment:
        if "模糊" in request:
            return ClarifyAssessment(confidence=0.2, restate="你想做X", question="具体要哪方面？")
        return ClarifyAssessment(confidence=0.95)

    return assess


def _graph():
    return build_fanout_recipe(
        MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        clarify_assess=_assess_by_keyword(),
    )


def _cfg(k):
    return {"configurable": {"thread_id": k}}


def _inbound(req: str):
    return {
        "group_key": "g",
        "roster": [AgentSlot(contact_id=c) for c in ("A", "B")],
        "pending_human": Msg(sender_id="u", sender_kind="human", text=req),
    }


async def test_clear_request_passes_through_to_curate():
    graph = _graph()
    out = await graph.ainvoke(_inbound("做一份便利店选址分析，预算30万"), _cfg("c1"))
    # 信心足 → clarify 直通 → frame → fanout → 停在 curate（非 clarify）
    assert out["__interrupt__"][0].value["type"] == "curate"


async def test_ambiguous_request_triggers_clarify_question():
    graph = _graph()
    out = await graph.ainvoke(_inbound("帮我搞个模糊的东西"), _cfg("c2"))
    payload = out["__interrupt__"][0].value
    assert payload["type"] == "clarify"
    assert payload["question"]  # 有澄清问
    assert payload["restate"]  # 有回述


async def test_skip_forces_into_frame():
    graph = _graph()
    cfg = _cfg("c3")
    await graph.ainvoke(_inbound("帮我搞个模糊的东西"), cfg)  # 停在 clarify
    out = await graph.ainvoke(Command(resume={"skip": True}), cfg)
    # 跳过 → 强制进 FRAME → fanout → 停在 curate
    assert out["__interrupt__"][0].value["type"] == "curate"


async def test_answer_is_merged_into_history():
    graph = _graph()
    cfg = _cfg("c4")
    await graph.ainvoke(_inbound("帮我搞个模糊的东西"), cfg)  # 停在 clarify
    await graph.ainvoke(Command(resume={"answer": "聚焦平价快消选品"}), cfg)
    snap = await graph.aget_state(cfg)
    hist = snap.values["history"]
    assert any(m.sender_kind == "human" and "聚焦平价快消选品" in m.text for m in hist)


async def test_no_assessor_passes_through():
    """未注入 assess（None）→ clarify 直通（保持 S1.4 占位行为，不打扰）。"""
    graph = build_fanout_recipe(MemorySaver(), assign=_fake_assign, generate=_fake_gen)
    out = await graph.ainvoke(_inbound("帮我搞个模糊的东西"), _cfg("c5"))
    assert out["__interrupt__"][0].value["type"] == "curate"  # 没在 clarify 停
