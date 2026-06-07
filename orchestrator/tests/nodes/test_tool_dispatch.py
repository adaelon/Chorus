"""S11c tool_dispatch: result closure, degradation, abort, and retry budget."""

from __future__ import annotations

from app.nodes.tool_dispatch import ToolDispatchError, tool_dispatch
from app.state import GroupState, RetryBudget, ToolCallIntent, ToolResult


def _intent(**kw) -> ToolCallIntent:
    data = {"call_id": "call-1", "kind": "sandbox_exec", "tool_name": "python"}
    data.update(kw)
    return ToolCallIntent(**data)


async def test_tool_dispatch_success_closes_pending_tool_result():
    async def execute(intent: ToolCallIntent):
        return ToolResult(
            call_id=intent.call_id,
            tool_name=intent.tool_name,
            ok=True,
            content="ok",
            data={"stdout": "ok\n"},
        )

    out = await tool_dispatch(
        GroupState(group_key="g", pending_tools=[_intent(requires_sandbox=True)]),
        execute=execute,
    )

    assert out["pending_tools"] == []
    assert out["tool_results"][0].ok is True
    assert out["tool_results"][0].data["stdout"] == "ok\n"
    assert out["sandbox_ready"] is True
    assert out["trace_events"][-1].node == "tool_dispatch"
    assert out["trace_events"][-1].status == "closed"


async def test_tool_dispatch_sandbox_down_writes_error_state_without_raising():
    async def execute(_intent: ToolCallIntent):
        raise ToolDispatchError(
            "sandbox_unavailable",
            "shipyard is not reachable",
            retryable=False,
            sandbox_ready=False,
        )

    out = await tool_dispatch(
        GroupState(group_key="g", pending_tools=[_intent(requires_sandbox=True)]),
        execute=execute,
    )

    assert out["pending_tools"] == []
    assert out["sandbox_ready"] is False
    assert out["run_status"] == "degraded"
    assert out["last_tool_error"].code == "sandbox_unavailable"
    assert out["tool_results"][0].ok is False
    assert out["tool_results"][0].error.code == "sandbox_unavailable"
    assert out["trace_events"][-1].status == "degraded"


async def test_tool_dispatch_abort_does_not_start_executor():
    calls: list[int] = []

    async def execute(_intent: ToolCallIntent):
        calls.append(1)
        return ToolResult(call_id="call-1", tool_name="python", ok=True)

    out = await tool_dispatch(
        GroupState(group_key="g", abort_requested=True, pending_tools=[_intent()]),
        execute=execute,
    )

    assert calls == []
    assert out["pending_tools"] == []
    assert out["run_status"] == "aborted"
    assert out["last_tool_error"].code == "aborted"
    assert out["tool_results"][0].error.code == "aborted"


async def test_tool_dispatch_retries_retryable_error_then_succeeds():
    calls: list[int] = []

    async def execute(intent: ToolCallIntent):
        calls.append(1)
        if len(calls) == 1:
            raise ToolDispatchError("temporary", "try again", retryable=True)
        return ToolResult(call_id=intent.call_id, tool_name=intent.tool_name, ok=True)

    out = await tool_dispatch(
        GroupState(
            group_key="g",
            retry_budget=RetryBudget(max_attempts=2),
            pending_tools=[_intent(kind="mcp_call", tool_name="search")],
        ),
        execute=execute,
    )

    assert len(calls) == 2
    assert out["tool_results"][0].ok is True
    assert out["retry_budget"].used_attempts == 0  # reset on success: next tool starts fresh
    assert "last_tool_error" not in out


async def test_tool_dispatch_retry_budget_exhaustion_writes_failed_result():
    calls: list[int] = []

    async def execute(_intent: ToolCallIntent):
        calls.append(1)
        raise ToolDispatchError("temporary", "still down", retryable=True)

    out = await tool_dispatch(
        GroupState(
            group_key="g",
            retry_budget=RetryBudget(max_attempts=2),
            pending_tools=[_intent(kind="mcp_call", tool_name="search")],
        ),
        execute=execute,
    )

    assert len(calls) == 2
    assert out["pending_tools"] == []
    assert out["run_status"] == "failed"
    assert out["last_tool_error"].code == "temporary"
    assert out["tool_results"][0].ok is False
    assert out["retry_budget"].used_attempts == 2
