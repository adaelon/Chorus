"""S5.4.2a 判据：配方库 CRUD 端点 + 四内置 seed + 内置不可删/改 + 写时校验（§6.16）。"""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.recipes.builtin import ROUNDTABLE
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


def test_builtins_seeded(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        ids = {r["id"] for r in client.get("/recipes").json()}
        assert {"fanout", "roundtable", "roundtable_continuous", "auto"} <= ids
        # 内置标记 + graph 可读回
        rt = client.get("/recipes/roundtable").json()
        assert rt["builtin"] is True and rt["graph"]["nodes"]


def test_custom_recipe_crud(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        g = copy.deepcopy(ROUNDTABLE)
        g["recipe"] = "my-rt"
        # create
        r = client.post("/recipes", json={"id": "my-rt", "name": "我的圆桌", "graph": g})
        assert r.status_code == 200 and r.json()["name"] == "我的圆桌"
        # duplicate → 409
        assert client.post("/recipes", json={"id": "my-rt", "name": "x", "graph": g}).status_code == 409
        # get
        assert client.get("/recipes/my-rt").json()["graph"]["recipe"] == "my-rt"
        # update
        r = client.put("/recipes/my-rt", json={"id": "my-rt", "name": "改名", "graph": g})
        assert r.status_code == 200 and r.json()["name"] == "改名"
        # delete
        assert client.delete("/recipes/my-rt").status_code == 200
        assert client.delete("/recipes/my-rt").status_code == 404


def test_builtin_is_read_only(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        assert client.delete("/recipes/roundtable").status_code == 403  # 内置不可删
        assert client.put(
            "/recipes/roundtable",
            json={"id": "roundtable", "name": "x", "graph": ROUNDTABLE},
        ).status_code == 403  # 内置不可改


def test_validate_endpoint(tmp_path):
    """S5.4.3c：/recipe/validate 返回人话错误列表（空=合法），不落库。"""
    with TestClient(_app(tmp_path)) as client:
        assert client.post("/recipe/validate", json={"graph": ROUNDTABLE}).json()["errors"] == []
        bad = {"recipe": "b", "version": 1,
               "nodes": [{"id": "frame", "use": "frame"}, {"id": "turn", "use": "turn"}],
               "edges": [{"from": "START", "to": "frame"}, {"from": "frame", "to": "turn"},
                         {"from": "turn", "to": "END"}]}
        errs = client.post("/recipe/validate", json={"graph": bad}).json()["errors"]
        assert any("needs" in m for m in errs)


def test_create_rejects_invalid_graph(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        bad = {"recipe": "bad", "version": 1,
               "nodes": [{"id": "frame", "use": "frame"}, {"id": "turn", "use": "turn"}],
               "edges": [{"from": "START", "to": "frame"}, {"from": "frame", "to": "turn"},
                         {"from": "turn", "to": "END"}]}  # turn.needs=next_speaker 未满足
        r = client.post("/recipes", json={"id": "bad", "name": "坏", "graph": bad})
        assert r.status_code == 422
        assert any("needs" in m for m in r.json()["detail"])
