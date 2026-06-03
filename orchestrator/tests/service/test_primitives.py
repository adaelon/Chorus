"""S5.4.3a 判据：GET /primitives 暴露 registry 机读契约（L4 画布卡片库，§6.16）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.service import create_app


def _app(tmp_path):
    return create_app(checkpointer=MemorySaver(), registry_db_path=str(tmp_path / "reg.sqlite"))


def test_primitives_endpoint(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.get("/primitives")
        assert r.status_code == 200
        prims = {p["name"]: p for p in r.json()}
        # 9 个用户可见原语全在
        assert set(prims) == {
            "clarify", "frame", "fanout", "turn",
            "schedule", "plan", "human_gate", "curate_gate", "synthesize",
        }
        # 契约字段：kind / needs / emits / budget
        assert prims["turn"]["kind"] == "transform"
        assert prims["turn"]["needs"] == ["next_speaker"]
        assert prims["schedule"]["kind"] == "router"
        assert set(prims["schedule"]["emits"]) == {"next_speaker", "yield_to_human", "stop"}
        assert prims["schedule"]["budget"] == {
            "count": "turns_since_human", "limit": "max_turns_per_human", "reason": "budget",
        }
        assert prims["frame"]["budget"] is None
