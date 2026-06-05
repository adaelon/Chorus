"""S11e minimal execution loop acceptance tests."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.execution_loop import build_execution_loop
from app.nodes.tool_dispatch import ToolDispatchError
from app.state import GroupState, ToolCallIntent, ToolResult


def _cfg(key: str):
    return {"configurable": {"thread_id": key}}


class _SequencedStream:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def __call__(self, _state: GroupState):
        self.calls += 1
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            yield '{"kind":"tool_call","call_id":"partial"'
            raise payload
        for chunk in payload:
            yield chunk


class _SequencedExecutor:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, intent: ToolCallIntent):
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
async def test_execution_loop_happy_path_tool_then_final():
    stream = _SequencedStream(_tool_payload(), ('{"kind":"final","text":"完成"}',))
    execute = _SequencedExecutor("ok")
    graph = build_execution_loop(MemorySaver(), stream=stream, execute=execute)

    out = await graph.ainvoke({"group_key": "happy"}, _cfg("happy"))

    assert stream.calls == 2
    assert execute.calls == 1
    assert out["pending_tools"] == []
    assert out["tool_results"][0].ok is True
    assert out["output"] == "完成"
    assert out["run_status"] == "done"


@pytest.mark.asyncio
async def test_execution_loop_reinvokes_llm_after_unclosed_chunk_crash():
    stream = _SequencedStream(RuntimeError("chunk crash"), ('{"kind":"final","text":"恢复后完成"}',))
    execute = _SequencedExecutor()
    graph = build_execution_loop(MemorySaver(), stream=stream, execute=execute)
    cfg = _cfg("unclosed")

    with pytest.raises(RuntimeError, match="chunk crash"):
        await graph.ainvoke({"group_key": "unclosed"}, cfg)
    snap = graph.get_state(cfg)
    assert "pending_tools" not in snap.values
    assert stream.calls == 1

    out = await graph.ainvoke(None, cfg)

    assert stream.calls == 2
    assert out["output"] == "恢复后完成"
    assert out["run_status"] == "done"


@pytest.mark.asyncio
async def test_execution_loop_does_not_reinvoke_llm_after_intent_closed_then_tool_crashes():
    stream = _SequencedStream(_tool_payload("call-closed"), ('{"kind":"final","text":"恢复后完成"}',))
    execute = _SequencedExecutor(RuntimeError("tool crash"), "ok")
    graph = build_execution_loop(MemorySaver(), stream=stream, execute=execute)
    cfg = _cfg("closed-intent")

    with pytest.raises(RuntimeError, match="tool crash"):
        await graph.ainvoke({"group_key": "closed-intent"}, cfg)
    snap = graph.get_state(cfg)
    assert snap.values["pending_tools"][0].call_id == "call-closed"
    assert stream.calls == 1
    assert execute.calls == 1

    out = await graph.ainvoke(None, cfg)

    assert stream.calls == 2  # second call is after tool result, not before dispatch retry
    assert execute.calls == 2
    assert out["tool_results"][0].ok is True
    assert out["output"] == "恢复后完成"
    assert out["run_status"] == "done"


@pytest.mark.asyncio
async def test_execution_loop_sandbox_down_degrades_without_crashing():
    stream = _SequencedStream(_tool_payload("down"))
    execute = _SequencedExecutor(
        ToolDispatchError(
            "sandbox_unavailable",
            "shipyard is down",
            retryable=False,
            sandbox_ready=False,
        )
    )
    graph = build_execution_loop(MemorySaver(), stream=stream, execute=execute)

    out = await graph.ainvoke({"group_key": "down"}, _cfg("down"))

    assert execute.calls == 1
    assert out["sandbox_ready"] is False
    assert out["last_tool_error"].code == "sandbox_unavailable"
    assert out["run_status"] == "degraded"
    assert "沙箱暂时不可用" in out["output"]


@pytest.mark.asyncio
async def test_execution_loop_abort_finishes_without_hanging_tool():
    stream = _SequencedStream(_tool_payload("abort"))
    execute = _SequencedExecutor("should-not-run")
    graph = build_execution_loop(MemorySaver(), stream=stream, execute=execute)

    out = await graph.ainvoke({"group_key": "abort", "abort_requested": True}, _cfg("abort"))
    state = GroupState(**out)

    assert execute.calls == 0
    assert state.pending_tools == []
    assert state.tool_results == []
    assert state.run_status == "aborted"
    assert state.output is None
