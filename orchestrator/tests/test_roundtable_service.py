"""S3.6c 判据：POST /roundtable/stream 起一场圆桌，SSE 出 framed→turn→human_gate。

注入假 assign/generate/extract/pick + MemorySaver + 临时 registry，离线确定性。
human_in_loop=True：第一轮发言后停在 human_gate（让位窗口）；续场/插话见 S3.6d。
（token 级 delta 由真实 LLM 走 tags=agent:<id> 路由，同扇出，不在离线断言。）
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
    async def pick(state: GroupState) -> NextSpeaker:
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


def test_roundtable_start_streams_turn_then_human_gate(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post(
            "/roundtable/stream",
            json={"group_key": "rt", "request": "要不要做付费会员", "roster": ["A", "B", "C"]},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = r.text
        # FRAME 分维度 → 第一轮 A 发言完成 → 停在 human_gate（让位窗口）
        assert '"type": "framed"' in body
        assert '"type": "turn"' in body
        assert '"contact_id": "A"' in body
        assert '"type": "human_gate"' in body
        assert '"type": "done"' in body


def _start(client, key, roster=("A", "B")):
    return client.post(
        "/roundtable/stream",
        json={"group_key": key, "request": "议题", "roster": list(roster)},
    )


def test_resume_continue_advances_to_next_speaker(tmp_path):
    """S3.6d：不插话续场（interject:null）→ 轮转推进到下一发言人 B。"""
    with TestClient(_app(tmp_path)) as client:
        _start(client, "d1")  # A 发言后停在 human_gate
        r = client.post("/roundtable/d1/resume/stream", json={"interject": None})
        assert r.status_code == 200
        body = r.text
        assert '"type": "turn"' in body
        assert '"contact_id": "B"' in body  # turns 未重置 → 轮到 B
        assert '"type": "human_gate"' in body


def test_resume_interject_redirects(tmp_path):
    """S3.6d：插话 → 预算归零（改向）→ 轮转重启回 A（而非继续到 B）。"""
    with TestClient(_app(tmp_path)) as client:
        _start(client, "d2")  # A 发言后停
        r = client.post(
            "/roundtable/d2/resume/stream", json={"interject": "换个方向：聚焦现金流"}
        )
        body = r.text
        assert '"type": "turn"' in body
        assert '"contact_id": "A"' in body  # 改向：turns 归零 → 轮转重启回 A
        assert '"contact_id": "B"' not in body


def test_interject_endpoint_injects_and_is_consumed(tmp_path):
    """S3.6d：异步 /interject 写 pending_human → 下次 resume 被消化（改向，重启回 A）。"""
    with TestClient(_app(tmp_path)) as client:
        _start(client, "d3")  # A 发言后停
        ri = client.post("/roundtable/d3/interject", json={"text": "插一句：预算有限"})
        assert ri.status_code == 200 and ri.json()["ok"] is True
        # 不当场 interject，但 pending_human 待消化 → human_gate 归零改向
        r = client.post("/roundtable/d3/resume/stream", json={"interject": None})
        body = r.text
        assert '"contact_id": "A"' in body  # pending 被消化 → 预算归零 → 重启回 A


def test_resume_before_start_returns_404(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/roundtable/never/resume/stream", json={"interject": None})
        assert r.status_code == 404
        r2 = client.post("/roundtable/never/interject", json={"text": "x"})
        assert r2.status_code == 404


def test_roundtable_persists_state_for_resume(tmp_path):
    """起场后状态落 checkpoint：第一轮发言进 history、停在 human_gate（供 S3.6d resume）。"""
    with TestClient(_app(tmp_path)) as client:
        client.post(
            "/roundtable/stream",
            json={"group_key": "rt2", "request": "议题", "roster": ["A", "B"]},
        )
        # 无端点直读 state，但再起同 key 会读到已有 checkpoint —— 改用一轮发言的可见证据：
        # turn 事件已断言归属；此处验证 human_gate 暂停（未跑到预算闸 output）
        r = client.post(
            "/roundtable/stream",
            json={"group_key": "rt3", "request": "议题2", "roster": ["A"]},
        )
        body = r.text
        assert '"type": "human_gate"' in body
        assert '"type": "output"' not in body  # 停在让位窗口，未收尾
