"""S11c: dispatch closed tool intents through an injected executor.

P0 locks the state semantics around success, sandbox degradation, abort, and
retry exhaustion. Real MCP/AstrBot/Shipyard wiring is intentionally deferred.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..run_ctx import current_group_key
from ..state import (
    AgentStep,
    GroupState,
    RetryBudget,
    ToolCallIntent,
    ToolResult,
    ToolRuntimeError,
    TraceEvent,
)

ToolExecutor = Callable[[ToolCallIntent], Awaitable[ToolResult]]


class ToolDispatchError(Exception):
    """Structured tool failure raised by fake/real executors."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        sandbox_ready: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.error = ToolRuntimeError(code=code, message=message, retryable=retryable)
        self.sandbox_ready = sandbox_ready


def _append_trace(
    state: GroupState,
    *,
    status: str,
    intent: ToolCallIntent,
    error: ToolRuntimeError | None = None,
    message: str | None = None,
) -> list[TraceEvent]:
    trace = list(state.trace_events)
    trace.append(
        TraceEvent(
            node="tool_dispatch",
            status=status,
            error=error.code if error else None,
            message=message,
            data={"call_id": intent.call_id, "tool_name": intent.tool_name},
        )
    )
    return trace


def _result_from_error(intent: ToolCallIntent, error: ToolRuntimeError) -> ToolResult:
    return ToolResult(
        call_id=intent.call_id,
        tool_name=intent.tool_name,
        ok=False,
        error=error,
    )


def _agent_step(state: GroupState, intent: ToolCallIntent, result: ToolResult) -> list[AgentStep]:
    """记一步进 scratchpad（S13a，§6.24）：planner 下轮据此知道'我跑了啥、得到啥'。"""
    return [
        *state.agent_steps,
        AgentStep(
            tool_name=intent.tool_name,
            args=dict(intent.args),
            ok=result.ok,
            content=result.content,
            error=result.error.code if result.error else None,
        ),
    ]


def _close_intent(state: GroupState) -> list[ToolCallIntent]:
    return list(state.pending_tools[1:])


async def tool_dispatch(
    state: GroupState,
    *,
    execute: ToolExecutor | None = None,
) -> dict:
    """Execute one closed pending tool intent and return a checkpointable delta."""
    if not state.pending_tools:
        return {}

    intent = state.pending_tools[0]
    if state.abort_requested:
        err = ToolRuntimeError(code="aborted", message="tool dispatch aborted", retryable=False)
        return {
            "pending_tools": _close_intent(state),
            "tool_results": [*state.tool_results, _result_from_error(intent, err)],
            "last_tool_error": err,
            "run_status": "aborted",
            "trace_events": _append_trace(state, status="aborted", intent=intent, error=err),
        }

    if execute is None:
        raise RuntimeError("tool_dispatch requires an injected executor in S11c P0")

    # S12c: expose the run's group_key to the executor so a SessionStore can
    # reuse one sandbox session per run (same pattern as turn/fanout, S7.3b).
    current_group_key.set(state.group_key)

    budget = state.retry_budget
    used_attempts = budget.used_attempts
    last_error: ToolRuntimeError | None = None
    sandbox_ready = state.sandbox_ready

    while used_attempts < budget.max_attempts:
        used_attempts += 1
        try:
            result = await execute(intent)
            return {
                "pending_tools": _close_intent(state),
                "tool_results": [*state.tool_results, result],
                "agent_steps": _agent_step(state, intent, result),
                "retry_budget": RetryBudget(
                    max_attempts=budget.max_attempts,
                    used_attempts=0,  # reset per-tool: each new tool starts fresh
                ),
                "sandbox_ready": True if intent.requires_sandbox else sandbox_ready,
                "trace_events": _append_trace(
                    state,
                    status="closed",
                    intent=intent,
                    message="tool_result",
                ),
            }
        except ToolDispatchError as exc:
            last_error = exc.error
            if exc.sandbox_ready is not None:
                sandbox_ready = exc.sandbox_ready
            if not exc.error.retryable:
                break

    err = last_error or ToolRuntimeError(
        code="tool_failed",
        message="tool dispatch failed",
        retryable=False,
    )
    status = "degraded" if sandbox_ready is False or err.code == "sandbox_unavailable" else "failed"
    result = _result_from_error(intent, err)
    return {
        "pending_tools": _close_intent(state),
        "tool_results": [*state.tool_results, result],
        "agent_steps": _agent_step(state, intent, result),
        "retry_budget": RetryBudget(max_attempts=budget.max_attempts, used_attempts=used_attempts),
        "sandbox_ready": sandbox_ready,
        "last_tool_error": err,
        "run_status": status,
        "trace_events": _append_trace(state, status=status, intent=intent, error=err),
    }
