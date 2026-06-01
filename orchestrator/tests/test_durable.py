"""S2.0 判据：AsyncSqliteSaver 让群状态跨"重启"存活（服务层版的 S1.1 判据）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.service import create_app
from app.state import AgentSlot, Candidate


async def _fake_assign(request, roster):
    return {s.contact_id: f"dim-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"[{slot.contact_id}] {request}")


def test_group_state_survives_restart(tmp_path):
    db = str(tmp_path / "chk.sqlite")

    # 第一次"进程"：inbound 写入候选（durable AsyncSqliteSaver，落 db 文件）
    with TestClient(create_app(db_path=db, assign=_fake_assign, generate=_fake_gen)) as c1:
        r = c1.post("/inbound", json={"group_key": "g", "request": "选题", "roster": ["A", "B"]})
        assert r.status_code == 200 and len(r.json()["candidates"]) == 2

    # 第二次"进程"：同一 db 文件重建 app，群候选仍在（synthesize 读 candidates）
    with TestClient(create_app(db_path=db, assign=_fake_assign, generate=_fake_gen)) as c2:
        r = c2.post("/synthesize", json={"group_key": "g"})
        assert r.status_code == 200
        out = r.json()["output"]
        assert "[A]" in out and "[B]" in out  # 候选跨重启存活
