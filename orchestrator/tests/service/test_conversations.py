"""S5.7a 判据：会话历史索引 + 读取（§6.17）。

起一场圆桌（停在 human_gate）→ /conversations 列得到、/conversations/{key} 返回含发言的
history + resumable=True；未知 key → 404。注入假节点 + 临时 registry，离线。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

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
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_conversation_indexed_and_readable(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # 起一场圆桌（human_in_loop → 第一轮后停在 human_gate）
        r = client.post(
            "/roundtable/stream",
            json={"group_key": "h1", "request": "要不要做付费会员", "roster": ["A", "B"]},
        )
        assert r.status_code == 200

        # 列表能列到，标题=议题
        lst = client.get("/conversations").json()
        assert any(c["id"] == "h1" and c["title"] == "要不要做付费会员" for c in lst)

        # 详情：含 A 的发言、resumable（停在 human_gate）
        d = client.get("/conversations/h1").json()
        assert d["id"] == "h1"
        assert any(m["sender_kind"] == "ai" and m["sender_id"] == "A" for m in d["history"])
        assert d["resumable"] is True
        assert {s["contact_id"] for s in d["roster"]} == {"A", "B"}


def test_conversation_order_recent_first(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        for k in ("c1", "c2", "c3"):
            client.post("/roundtable/stream", json={"group_key": k, "request": f"题{k}", "roster": ["A"]})
        ids = [c["id"] for c in client.get("/conversations").json()]
        assert ids[:3] == ["c3", "c2", "c1"]  # 近→远


def test_unknown_conversation_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/conversations/nope").status_code == 404
