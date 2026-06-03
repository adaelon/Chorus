"""S1.6 判据：端到端 /inbound → N 候选 → /curate → 策展结果 → /synthesize，序列正确。

注入假 assign/generate + MemorySaver + 临时 registry，离线确定性（不碰真实 LLM、不在 cwd 落文件）。
lifespan 需用 `with TestClient(app)` 触发。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.service import create_app
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}] {request}")


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_health(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"


def test_fanout_e2e_inbound_curate_synthesize(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # /inbound → 3 份候选
        r = client.post("/inbound", json={"group_key": "g1", "request": "便利店选题", "roster": ["A", "B", "C"]})
        assert r.status_code == 200
        cands = r.json()["candidates"]
        assert len(cands) == 3
        assert {c["contact_id"] for c in cands} == {"A", "B", "C"}
        assert all("便利店选题" in c["text"] for c in cands)

        # /curate → eliminate A + pick B + reassign 一个点给 C
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


def test_curate_before_inbound_returns_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/synthesize", json={"group_key": "never"})
        assert r.status_code == 404
