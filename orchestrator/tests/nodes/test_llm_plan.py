"""S11b llm_plan: chunk aggregation and checkpoint boundary."""

from __future__ import annotations

import pytest

from app.nodes.llm_plan import llm_plan
from app.state import GroupState, SkillRef, ToolCallIntent, TraceEvent


def _stream(*chunks: str, calls: list[int] | None = None):
    async def s(_state: GroupState):
        if calls is not None:
            calls.append(1)
        for chunk in chunks:
            yield chunk

    return s


def _crashing_stream(calls: list[int] | None = None):
    async def s(_state: GroupState):
        if calls is not None:
            calls.append(1)
        yield '{"kind":"tool_call","call_id":"call-1"'
        raise RuntimeError("stream interrupted")

    return s


async def test_llm_plan_closes_tool_intent_after_full_stream():
    out = await llm_plan(
        GroupState(group_key="g"),
        stream=_stream(
            '{"kind":"tool_',
            'call","call_id":"call-1","tool_kind":"sandbox_exec",',
            '"tool_name":"python","args":{"code":"print(1)"},',
            '"requires_sandbox":true,"skill_refs":[{"name":"software-engineering"}]}',
        ),
    )

    assert "output" not in out
    intent = out["pending_tools"][0]
    assert intent.call_id == "call-1"
    assert intent.kind == "sandbox_exec"
    assert intent.tool_name == "python"
    assert intent.args["code"] == "print(1)"
    assert intent.requires_sandbox is True
    assert intent.skill_refs == [SkillRef(name="software-engineering")]
    assert out["trace_events"][-1].node == "llm_plan"
    assert out["trace_events"][-1].status == "closed"


async def test_llm_plan_closes_final_message_after_full_stream():
    out = await llm_plan(
        GroupState(group_key="g"),
        stream=_stream('{"kind":"final","text":"沙箱暂时不可用"}'),
    )

    assert out["output"] == "沙箱暂时不可用"
    assert out["run_status"] == "done"
    assert out["trace_events"][-1].message == "final"


async def test_llm_plan_does_not_return_delta_when_stream_crashes_before_close():
    with pytest.raises(RuntimeError, match="stream interrupted"):
        await llm_plan(GroupState(group_key="g"), stream=_crashing_stream())


async def test_llm_plan_abort_does_not_call_stream():
    calls: list[int] = []

    out = await llm_plan(
        GroupState(group_key="g", abort_requested=True),
        stream=_stream('{"kind":"final","text":"x"}', calls=calls),
    )

    assert calls == []
    assert out["run_status"] == "aborted"
    assert out["trace_events"][-1].status == "aborted"


async def test_llm_plan_reinvokes_after_unclosed_crash_because_state_has_no_delta():
    calls: list[int] = []
    state = GroupState(group_key="g")
    with pytest.raises(RuntimeError):
        await llm_plan(state, stream=_crashing_stream(calls))

    assert state.pending_tools == []
    assert state.trace_events == []

    out = await llm_plan(
        state,
        stream=_stream(
            '{"kind":"tool_call","call_id":"call-2","tool_kind":"mcp_call","tool_name":"search"}',
            calls=calls,
        ),
    )
    assert len(calls) == 2
    assert out["pending_tools"][0].call_id == "call-2"


async def test_llm_plan_does_not_reinvoke_when_intent_already_closed():
    calls: list[int] = []
    state = GroupState(
        group_key="g",
        pending_tools=[
            ToolCallIntent(call_id="call-1", kind="mcp_call", tool_name="search")
        ],
    )

    out = await llm_plan(state, stream=_stream('{"kind":"final","text":"x"}', calls=calls))

    assert out == {}
    assert calls == []


async def test_llm_plan_does_not_reinvoke_when_final_already_closed():
    calls: list[int] = []
    state = GroupState(
        group_key="g",
        output="done",
        trace_events=[TraceEvent(node="llm_plan", status="closed", message="final")],
    )

    out = await llm_plan(state, stream=_stream('{"kind":"final","text":"x"}', calls=calls))

    assert out == {}
    assert calls == []
