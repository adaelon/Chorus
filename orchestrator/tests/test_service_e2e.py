"""S1.6 判据：端到端 /inbound → N 候选 → /curate → 策展结果 → /synthesize，序列正确。

注入假 assign/generate，离线确定性验证（不碰真实 LLM）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.service import create_app
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}] {request}")


def _client() -> TestClient:
    return TestClient(create_app(assign=_fake_assign, generate=_fake_gen))


def test_fanout_e2e_inbound_curate_synthesize():
    client = _client()

    # /inbound → 3 份候选
    r = client.post("/inbound", json={"group_key": "g1", "request": "便利店选题", "roster": ["A", "B", "C"]})
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert len(cands) == 3
    assert {c["contact_id"] for c in cands} == {"A", "B", "C"}
    assert all("便利店选题" in c["text"] for c in cands)

    # /curate → eliminate A + reassign 一个点给 B
    r2 = client.post(
        "/curate",
        json={
            "group_key": "g1",
            "commands": [
                {"kind": "eliminate", "contact_id": "A"},
                {"kind": "pick", "contact_id": "B", "point": "B 的要点"},
                {"kind": "reassign", "point": "A 提的现金流视角", "executor_id": "C"},
            ],
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    ids = {c["contact_id"] for c in body["candidates"]}
    assert "A" not in ids  # eliminate 生效
    assert any("A 提的现金流视角" in c["text"] for c in body["candidates"] if c["contact_id"] == "C")  # reassign
    assert any(c["text"] == "B 的要点" for c in body["picked"])  # pick

    # /synthesize → 汇出含 picked 的产出
    r3 = client.post("/synthesize", json={"group_key": "g1"})
    assert r3.status_code == 200
    assert "B 的要点" in r3.json()["output"]


def test_curate_before_inbound_returns_404():
    # 未 /inbound 的群没有 state → 404，而非静默成功
    client = _client()
    r = client.post("/synthesize", json={"group_key": "never"})
    assert r.status_code == 404
