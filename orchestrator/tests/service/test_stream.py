"""S(流式) 判据：/inbound/stream 发出 SSE 事件序列 framed → candidates → done。

离线注入假 generate（不产真实 token deltas）；真实 token 路由由 spike 验证（tags=agent:<id>）。
"""

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


def test_inbound_stream_emits_sse_sequence(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/inbound/stream",
            json={"group_key": "g", "request": "选题", "roster": ["A", "B"]},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = r.text
        # 事件序列：framed（维度）→ candidates（最终）→ done
        assert '"type": "framed"' in body
        assert '"type": "candidates"' in body
        assert '"type": "done"' in body

        # 流式生成后，候选已落 checkpoint：/curate 可用
        r2 = client.post(
            "/curate",
            json={"group_key": "g", "commands": [{"kind": "eliminate", "contact_id": "A"}]},
        )
        assert r2.status_code == 200
        assert "A" not in {c["contact_id"] for c in r2.json()["candidates"]}
