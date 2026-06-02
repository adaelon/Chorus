"""S3.6a 判据：扇出 /inbound 接 live CLARIFY，按 payload.type 分流。

注入假 assess/assign/generate + MemorySaver + 临时 registry，离线确定性。
模糊需求 → /inbound 返回 type=clarify（回述+一问）；/clarify answer 并入 history、skip 强制进
FRAME，两者续跑到 curate → type=candidates。清晰需求直通 candidates。
未注入 assess（既有 e2e）行为不变由 test_service_e2e 保证。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.clarify import ClarifyAssessment
from app.service import create_app
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}]")


def _assess_by_keyword():
    """含"模糊"→信心低（要澄清），否则高（直通）。"""

    async def assess(request: str) -> ClarifyAssessment:
        if "模糊" in request:
            return ClarifyAssessment(confidence=0.2, restate="你想做X", question="具体哪方面？")
        return ClarifyAssessment(confidence=0.95)

    return assess


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        clarify_assess=_assess_by_keyword(),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_clear_request_passes_through_to_candidates(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/inbound",
            json={"group_key": "cg1", "request": "做一份便利店选址分析，预算30万", "roster": ["A", "B"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "candidates"
        assert {c["contact_id"] for c in body["candidates"]} == {"A", "B"}


def test_ambiguous_request_returns_clarify(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/inbound", json={"group_key": "cg2", "request": "帮我搞个模糊的东西", "roster": ["A", "B"]}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "clarify"
        assert body["restate"] and body["question"]


def test_clarify_answer_merges_and_continues_to_candidates(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        client.post(
            "/inbound", json={"group_key": "cg3", "request": "帮我搞个模糊的东西", "roster": ["A", "B"]}
        )
        r = client.post("/clarify", json={"group_key": "cg3", "answer": "聚焦平价快消选品"})
        assert r.status_code == 200
        assert r.json()["type"] == "candidates"


def test_clarify_skip_forces_into_candidates(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        client.post(
            "/inbound", json={"group_key": "cg4", "request": "帮我搞个模糊的东西", "roster": ["A", "B"]}
        )
        r = client.post("/clarify", json={"group_key": "cg4", "skip": True})
        assert r.status_code == 200
        assert r.json()["type"] == "candidates"


def test_clarify_before_inbound_returns_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/clarify", json={"group_key": "never", "skip": True})
        assert r.status_code == 404
