"""S2.4 判据：Contact 注册表 CRUD 端点；live curate eliminate 写信誉落库。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.service import create_app
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}]")


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_contact_crud(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # create
        r = client.post("/contacts", json={"id": "laochen", "name": "老陈", "title": "经济顾问"})
        assert r.status_code == 200 and r.json()["name"] == "老陈"
        # duplicate → 409
        assert client.post("/contacts", json={"id": "laochen", "name": "x"}).status_code == 409
        # list
        assert any(c["id"] == "laochen" for c in client.get("/contacts").json())
        # update
        r = client.put("/contacts/laochen", json={"id": "laochen", "name": "老陈", "title": "首席经济顾问"})
        assert r.status_code == 200 and r.json()["title"] == "首席经济顾问"
        # delete
        assert client.delete("/contacts/laochen").status_code == 200
        assert client.delete("/contacts/laochen").status_code == 404  # 已删


def test_live_eliminate_writes_reputation(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        client.post("/contacts", json={"id": "A", "name": "老陈"})
        client.post("/contacts", json={"id": "B", "name": "小杨"})
        client.post("/inbound", json={"group_key": "g", "request": "q", "roster": ["A", "B"]})

        client.post("/curate", json={"group_key": "g", "commands": [{"kind": "eliminate", "contact_id": "A"}]})

        # eliminate 经 live reputation_adjuster 落库：A 信誉降，且 A 仍在（可被邀）
        a = next(c for c in client.get("/contacts").json() if c["id"] == "A")
        assert a["reputation"] == -1.0
