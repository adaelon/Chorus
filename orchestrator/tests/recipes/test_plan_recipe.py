"""S5.5 判据：L3 AI 产出 DAG——plan_recipe 出合法图 + /recipe/auto 存库可跑（§6.16）。

注入假 recipe_planner 离线确定性。验：圆桌/扇出/去 clarify 各出合法图；端点存库 + 跑通。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.recipes import RecipePlan, plan_recipe
from app.recipes.validate import validate_recipe
from app.service import create_app
from app.state import AgentSlot, Candidate, Claim, GroupState


def _planner(plan: RecipePlan):
    async def p(task, roster):
        return plan

    return p


async def test_plan_recipe_roundtable_valid():
    name, graph = await plan_recipe("要不要做付费会员", ["A", "B"], planner=_planner(RecipePlan()))
    assert validate_recipe(graph) == []
    assert any(n["id"] == "clarify" for n in graph["nodes"])  # 默认含澄清
    assert "圆桌" in name


async def test_plan_recipe_fanout_valid():
    _, graph = await plan_recipe("给我三版文案", ["A", "B"], planner=_planner(RecipePlan(mode="fanout")))
    assert validate_recipe(graph) == []
    assert any(n["use"] == "curate_gate" for n in graph["nodes"])  # 扇出含策展


async def test_plan_recipe_drop_clarify_valid():
    _, graph = await plan_recipe(
        "x", ["A"], planner=_planner(RecipePlan(clarify=False, human_in_loop=False))
    )
    assert validate_recipe(graph) == []
    assert not any(n["id"] == "clarify" for n in graph["nodes"])  # 去掉了澄清
    assert any(e["from"] == "START" and e["to"] == "frame" for e in graph["edges"])  # START 接到 frame


async def test_plan_recipe_no_planner_defaults_roundtable():
    _, graph = await plan_recipe("任意", ["A"])
    assert validate_recipe(graph) == []


# ---- /recipe/auto 端点：存库 + 可跑 ----


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot, request, history, claims=None):
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 发言")


async def _fake_extract(text, speaker_id, turn_idx):
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState):
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        pick=_round_robin(),
        # 假 recipe_planner：选连续圆桌、不澄清（离线一气呵成跑到 output）
        recipe_planner=_planner(RecipePlan(mode="roundtable", clarify=False, human_in_loop=False)),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_recipe_auto_saves_and_runs(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/recipe/auto", json={"task": "要不要做付费会员", "roster": ["A", "B"]})
        assert r.status_code == 200
        rec = r.json()
        assert rec["builtin"] is False and rec["graph"]["nodes"]
        # 落库可见
        assert any(x["id"] == rec["id"] for x in client.get("/recipes").json())
        # 端到端跑：AI 搭的图经 /recipe/run 跑到 output（S5.5 闭环）
        run = client.post(
            "/recipe/run",
            json={"recipe_id": rec["id"], "group_key": "g", "request": "议题", "roster": ["A", "B"], "max_turns_per_human": 2},
        )
        assert run.status_code == 200
        assert '"type": "output"' in run.text
