"""S12e: POST /execution/run wires the execution loop with a real executor + SSE.

TestClient e2e with FakeBackend + a fake planner stream (narrow JSON intent
protocol). Verifies trace/output emission, sandbox-down degradation, entry
cancellation, sandbox session release at run end, and 503 when unconfigured.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.execution_sandbox import FakeBackend
from app.service import create_app

_TOOL = {
    "kind": "tool_call",
    "call_id": "t1",
    "tool_kind": "sandbox_exec",
    "tool_name": "python",
    "args": {"command": "echo hi"},
    "requires_sandbox": True,
}
_FINAL = {"kind": "final", "text": "all done"}


def _stream(scripts):
    """Fake planner: emit scripts[i] (JSON) on the i-th llm_plan call."""
    n = {"i": 0}

    async def stream(_state):
        i = n["i"]
        n["i"] += 1
        yield json.dumps(scripts[min(i, len(scripts) - 1)])

    return stream


def _events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _app(tmp_path, *, scripts, backend):
    return create_app(
        checkpointer=MemorySaver(),
        execution_checkpointer=MemorySaver(),
        execution_stream=_stream(scripts),
        sandbox_backend=backend,
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_execution_run_happy_path_emits_trace_and_output(tmp_path):
    app = _app(tmp_path, scripts=[_TOOL, _FINAL], backend=FakeBackend(stdout="hi\n"))
    with TestClient(app) as client:
        r = client.post("/execution/run", json={"group_key": "ex1", "request": "do it"})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        events = _events(r.text)
        types = [e["type"] for e in events]
        assert "trace" in types
        assert any(e["type"] == "trace" and e["node"] == "tool_dispatch" for e in events)
        assert any(e["type"] == "output" and e["output"] == "all done" for e in events)
        assert events[-1]["type"] == "done"


def test_execution_run_sandbox_down_degrades(tmp_path):
    app = _app(tmp_path, scripts=[_TOOL, _FINAL], backend=FakeBackend(ready=False))
    with TestClient(app) as client:
        r = client.post("/execution/run", json={"group_key": "ex2", "request": "do it"})
        events = _events(r.text)
        assert any(e.get("run_status") == "degraded" for e in events)
        assert events[-1]["type"] == "done"


def test_execution_run_entry_abort_is_observable(tmp_path):
    app = _app(tmp_path, scripts=[_FINAL], backend=FakeBackend())
    with TestClient(app) as client:
        r = client.post(
            "/execution/run", json={"group_key": "ex3", "request": "x", "abort": True}
        )
        events = _events(r.text)
        assert any(e.get("run_status") == "aborted" for e in events)


def test_execution_run_releases_sandbox_session_at_run_end(tmp_path):
    backend = FakeBackend(stdout="hi\n")
    app = _app(tmp_path, scripts=[_TOOL, _FINAL], backend=backend)
    with TestClient(app) as client:
        client.post("/execution/run", json={"group_key": "ex4", "request": "do it"})
    # one session reused across the run (S12c), closed on release at run end
    assert len(backend.sessions) == 1
    assert backend.sessions[0].closed is True


def test_execution_run_not_configured_returns_503(tmp_path):
    app = create_app(checkpointer=MemorySaver(), registry_db_path=str(tmp_path / "reg.sqlite"))
    with TestClient(app) as client:
        r = client.post("/execution/run", json={"group_key": "ex5", "request": "x"})
        assert r.status_code == 503
