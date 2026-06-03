"""S5.8a 判据：出错重试——节点抛错后 /session/{key}/retry/stream 断点续跑（§6.17）。

verify LangGraph `astream(None)` 在节点报错后从最后 checkpoint 重跑挂起节点：
首轮 turn 的 generate 抛错→SSE 出 error；retry→generate 二调成功→出 turn/human_gate；
且 frame 的 assign 只跑一次（证明断点续、非整场重来）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.schedule import NextSpeaker
from app.service import create_app
from app.state import AgentSlot, Candidate, Claim, GroupState


class _CountingAssign:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request, roster):
        self.calls += 1
        return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


class _FlakyGen:
    """首调抛错（模拟流式中断），其后成功。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, slot: AgentSlot, request: str, history, claims=None) -> Candidate:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom: 流式中断")
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 的发言")


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState):
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def test_retry_resumes_after_node_error(tmp_path):
    assign = _CountingAssign()
    gen = _FlakyGen()
    app = create_app(
        checkpointer=MemorySaver(),
        assign=assign,
        generate=gen,
        extract=_fake_extract,
        pick=_round_robin(),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )
    with TestClient(app) as client:
        # 起场：clarify/frame/schedule 过 → turn 的 generate 首调抛错 → SSE 出 error
        r1 = client.post("/roundtable/stream", json={"group_key": "e1", "request": "议题", "roster": ["A"]})
        assert r1.status_code == 200
        assert '"type": "error"' in r1.text
        assert '"type": "human_gate"' not in r1.text  # 没跑到让位窗口

        # 重试：astream(None) 从最后 checkpoint 重跑 turn → 这次成功 → turn + human_gate
        r2 = client.post("/session/e1/retry/stream")
        assert r2.status_code == 200
        assert '"type": "turn"' in r2.text and '"contact_id": "A"' in r2.text
        assert '"type": "human_gate"' in r2.text

        # 断点续证据：frame 的 assign 只跑了一次（重试没重跑 frame），generate 跑了两次（错+对）
        assert assign.calls == 1
        assert gen.calls == 2
