"""S13d: a roundtable turn is tool-enabled when execution is configured.

Offline e2e proving the wire: create_app(execution_stream + sandbox_backend)
threads plan_stream/execute into the roundtable turn, so an AI's turn runs the
tool phase and the SSE carries tool_call/tool_result sub-events — while still
producing the speech. Without execution the roundtable is unchanged (covered by
test_roundtable_service, A3).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.execution_sandbox import FakeBackend
from app.nodes.schedule import NextSpeaker
from app.service import create_app
from app.state import AgentSlot, Candidate, Claim, GroupState

_TOOL = {
    "kind": "tool_call",
    "call_id": "c1",
    "tool_kind": "sandbox_exec",
    "tool_name": "shell",
    "args": {"command": "python3 -c 'print(55)'"},
    "requires_sandbox": True,
}
_FINAL = {"kind": "final", "text": "done"}


def _plan(scripts):
    n = {"i": 0}

    async def stream(_state):
        i = n["i"]
        n["i"] += 1
        yield json.dumps(scripts[min(i, len(scripts) - 1)])

    return stream


async def _fake_assign(request, roster):
    return {s.contact_id: f"维度-{s.contact_id}" for s in roster}


async def _fake_gen(slot: AgentSlot, request: str, history, claims=None) -> Candidate:
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text=f"{slot.contact_id} 说")


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text="pt", turn=turn_idx)]


def _round_robin():
    async def pick(state: GroupState) -> NextSpeaker:
        ids = [s.contact_id for s in state.roster]
        return NextSpeaker(contact_id=ids[state.turns_since_human % len(ids)])

    return pick


def _tool_app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        execution_checkpointer=MemorySaver(),
        assign=_fake_assign,
        generate=_fake_gen,
        extract=_fake_extract,
        pick=_round_robin(),
        execution_stream=_plan([_TOOL, _FINAL]),
        sandbox_backend=FakeBackend(stdout="55"),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_roundtable_turn_streams_tool_subevents_then_speaks(tmp_path):
    with TestClient(_tool_app(tmp_path)) as client:
        r = client.post(
            "/roundtable/stream",
            json={"group_key": "rtx", "request": "算 fib(10)", "roster": ["A", "B"]},
        )
        assert r.status_code == 200
        body = r.text
        # the AI's turn ran the tool phase: sub-events surface, attributed to A
        assert '"type": "tool_call"' in body
        assert '"type": "tool_result"' in body
        assert '"speaker_id": "A"' in body
        assert '"content": "55"' in body
        # and still produced the speech + paused at human_gate (flow unchanged, β)
        assert '"type": "turn"' in body
        assert '"type": "human_gate"' in body
