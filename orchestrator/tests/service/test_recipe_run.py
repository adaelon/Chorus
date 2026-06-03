"""S5.4.2b 判据：POST /recipe/run 按 id 取库内配方→编译→SSE 流式跑（§6.16）。

注入假 assign/generate/extract/pick/planner + MemorySaver + 临时 registry，离线确定性。
验：内置 roundtable_continuous 跑到 output；auto（假 planner）也能跑到 output（S5.5 复用此路）；
未知 recipe_id → 404。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.plan import Speak, Synthesize
from app.nodes.schedule import NextSpeaker
from app.service import create_app
from app.state import AgentSlot, Candidate, Claim, GroupState


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的发言")


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _seq_planner(decisions):
    it = iter(decisions)

    async def planner(state: GroupState):
        return next(it, Synthesize())

    return planner


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        pick=_round_robin(),
        planner=_seq_planner([Speak(contact_id="A"), Speak(contact_id="B"), Synthesize()]),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_run_builtin_roundtable_continuous_to_output(tmp_path):
    """内置 roundtable_continuous（无人在环）：预算闸停 → 主笔综合 → output→END。"""
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/recipe/run",
            json={
                "recipe_id": "roundtable_continuous",
                "group_key": "rr1",
                "request": "要不要做付费会员",
                "roster": ["A", "B"],
                "max_turns_per_human": 2,
            },
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = r.text
        assert '"type": "framed"' in body
        assert '"type": "turn"' in body
        assert '"type": "output"' in body  # 跑到收尾
        assert '"type": "human_gate"' not in body  # 连续版无让位窗口
        assert '"type": "done"' in body


def test_run_builtin_auto_to_output(tmp_path):
    """内置 auto（L3）：假 planner 出 Speak A/B→Synthesize → 一气呵成到 output（S5.5 预演）。"""
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/recipe/run",
            json={"recipe_id": "auto", "group_key": "rr2", "request": "任务X", "roster": ["A", "B"]},
        )
        assert r.status_code == 200
        body = r.text
        assert '"type": "turn"' in body
        assert '"type": "output"' in body


def test_run_unknown_recipe_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/recipe/run",
            json={"recipe_id": "nope", "group_key": "rr3", "request": "x", "roster": ["A"]},
        )
        assert r.status_code == 404
