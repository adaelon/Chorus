"""S11f P1 durability, StreamHealth, and audit projection for execution loop."""

from __future__ import annotations

import asyncio

import pytest

from app.execution_loop import build_execution_loop
from app.execution_runtime import (
    execution_checkpointer,
    execution_sse,
    filter_audit_rows,
    project_trace_events,
    stream_with_heartbeat,
)
from app.state import GroupState, ToolCallIntent, ToolResult, TraceEvent


def _cfg(key: str):
    return {"configurable": {"thread_id": key}}


class _RecordingStream:
    def __init__(self, events: list[str], *payloads):
        self.events = events
        self.payloads = list(payloads)
        self.calls = 0

    async def __call__(self, _state: GroupState):
        self.events.append("stream")
        self.calls += 1
        for chunk in self.payloads.pop(0):
            yield chunk


class _RecordingExecutor:
    def __init__(self, events: list[str], *responses):
        self.events = events
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, intent: ToolCallIntent):
        self.events.append("execute")
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return ToolResult(
            call_id=intent.call_id,
            tool_name=intent.tool_name,
            ok=True,
            content=str(response),
        )


def _tool_payload(call_id: str = "call-1") -> tuple[str, ...]:
    return (
        '{"kind":"tool_call","call_id":"',
        call_id,
        '","tool_kind":"sandbox_exec","tool_name":"python",',
        '"requires_sandbox":true,"args":{"code":"print(1)"}}',
    )


@pytest.mark.asyncio
async def test_execution_loop_resumes_closed_intent_after_durable_restart(tmp_path):
    db = tmp_path / "execution.sqlite"
    cfg = _cfg("durable-execution")

    events1: list[str] = []
    stream1 = _RecordingStream(events1, _tool_payload("closed-before-restart"))
    execute1 = _RecordingExecutor(events1, RuntimeError("tool crash"))
    async with execution_checkpointer(db) as cp1:
        graph1 = build_execution_loop(cp1, stream=stream1, execute=execute1)
        with pytest.raises(RuntimeError, match="tool crash"):
            await graph1.ainvoke({"group_key": "durable-execution"}, cfg)
        snap1 = await graph1.aget_state(cfg)
        assert snap1.values["pending_tools"][0].call_id == "closed-before-restart"
        assert events1 == ["stream", "execute"]

    events2: list[str] = []
    stream2 = _RecordingStream(events2, ('{"kind":"final","text":"恢复完成"}',))
    execute2 = _RecordingExecutor(events2, "ok")
    async with execution_checkpointer(db) as cp2:
        graph2 = build_execution_loop(cp2, stream=stream2, execute=execute2)
        out = await graph2.ainvoke(None, cfg)

    assert events2 == ["execute", "stream"]
    assert out["tool_results"][0].ok is True
    assert out["output"] == "恢复完成"
    assert out["run_status"] == "done"


@pytest.mark.asyncio
async def test_stream_with_heartbeat_emits_idle_comment_before_next_event():
    async def source():
        await asyncio.sleep(0.03)
        yield execution_sse({"type": "done"})

    frames = []
    async for frame in stream_with_heartbeat(source(), interval=0.005):
        frames.append(frame)
        if frame.startswith("data:"):
            break

    assert ": heartbeat\n\n" in frames[:-1]
    assert frames[-1] == 'data: {"type": "done"}\n\n'


def test_projected_trace_events_are_queryable_by_thread_run_node_and_status():
    rows = project_trace_events(
        [
            TraceEvent(node="llm_plan", run_id="run-1", status="closed", message="tool_call"),
            TraceEvent(
                node="tool_dispatch",
                run_id="run-1",
                status="degraded",
                error="sandbox_unavailable",
                data={"call_id": "call-1"},
            ),
            TraceEvent(node="tool_dispatch", run_id="run-1", status="retry"),
            TraceEvent(node="llm_plan", run_id="run-2", status="closed", message="final"),
        ],
        thread_id="thread-1",
    )

    degraded = filter_audit_rows(
        rows,
        thread_id="thread-1",
        run_id="run-1",
        node="tool_dispatch",
        status="degraded",
    )

    assert len(degraded) == 1
    assert degraded[0]["error"] == "sandbox_unavailable"
    assert degraded[0]["data"]["call_id"] == "call-1"
    assert len(filter_audit_rows(rows, node="tool_dispatch", status="retry")) == 1
    assert filter_audit_rows(rows, run_id="run-missing") == []
