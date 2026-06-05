"""S11a execution contract state: defaults, serialization, and field registry."""

from __future__ import annotations

from app.graph import build_app, make_checkpointer
from app.recipes.spec import STATE_FIELDS
from app.state import (
    GroupState,
    RetryBudget,
    SkillRef,
    ToolCallIntent,
    ToolResult,
    ToolRuntimeError,
    TraceEvent,
)


def test_s11a_defaults_hydrate_from_legacy_state():
    state = GroupState(group_key="legacy")

    assert state.trace_events == []
    assert state.run_status == "running"
    assert state.retry_budget == RetryBudget()
    assert state.abort_requested is False
    assert state.pending_tools == []
    assert state.tool_results == []
    assert state.sandbox_ready is None
    assert state.last_tool_error is None


def test_s11a_trace_event_has_observability_fields():
    event = TraceEvent(
        node="llm_plan",
        run_id="run-1",
        status="error",
        error="stream closed",
        data={"chunk_count": 3},
    )

    dumped = event.model_dump()
    assert {"node", "run_id", "status", "error"} <= set(dumped)
    assert dumped["data"]["chunk_count"] == 3


def test_s11a_tool_contract_round_trips_nested_models():
    err = ToolRuntimeError(
        code="sandbox_unavailable",
        message="shipyard is not reachable",
        retryable=True,
        detail={"endpoint": "http://shipyard"},
    )
    intent = ToolCallIntent(
        call_id="call-1",
        kind="mcp_call",
        tool_name="search_files",
        args={"query": "README"},
        skill_refs=[SkillRef(name="software-engineering")],
        requires_sandbox=True,
        sandbox_profile="python-default",
        timeout_ms=30_000,
    )
    result = ToolResult(call_id="call-1", tool_name="search_files", ok=False, error=err)
    state = GroupState(
        group_key="g",
        trace_events=[TraceEvent(node="tool_dispatch", status="degraded", error=err.code)],
        run_status="degraded",
        retry_budget=RetryBudget(max_attempts=3, used_attempts=2),
        abort_requested=True,
        pending_tools=[intent],
        tool_results=[result],
        sandbox_ready=False,
        last_tool_error=err,
    )

    dumped = state.model_dump(mode="json")
    rehydrated = GroupState(**dumped)

    assert rehydrated.pending_tools[0].kind == "mcp_call"
    assert rehydrated.pending_tools[0].skill_refs[0].entry == "SKILL.md"
    assert rehydrated.tool_results[0].error is not None
    assert rehydrated.last_tool_error is not None
    assert rehydrated.last_tool_error.code == "sandbox_unavailable"


def test_s11a_execution_contract_persists_through_checkpointer(tmp_path):
    db = tmp_path / "s11a.sqlite"
    cfg = {"configurable": {"thread_id": "s11a"}}
    cp = make_checkpointer(db)
    app = build_app(cp)

    app.invoke(
        {
            "group_key": "s11a",
            "trace_events": [
                TraceEvent(node="llm_plan", run_id="run-1", status="closed").model_dump()
            ],
            "run_status": "running",
            "retry_budget": RetryBudget(max_attempts=2, used_attempts=1).model_dump(),
            "pending_tools": [
                ToolCallIntent(
                    call_id="call-1",
                    kind="sandbox_exec",
                    tool_name="python",
                    args={"code": "print('ok')"},
                    skill_refs=[SkillRef(name="imagegen")],
                    requires_sandbox=True,
                ).model_dump()
            ],
            "sandbox_ready": True,
        },
        cfg,
    )
    snap = app.get_state(cfg)
    cp.conn.close()

    rehydrated = GroupState(**snap.values)
    assert rehydrated.trace_events[0].node == "llm_plan"
    assert rehydrated.retry_budget.used_attempts == 1
    assert rehydrated.pending_tools[0].kind == "sandbox_exec"
    assert rehydrated.pending_tools[0].skill_refs[0].name == "imagegen"
    assert rehydrated.sandbox_ready is True


def test_s11a_fields_are_visible_to_recipe_specs():
    for field in (
        "trace_events",
        "run_status",
        "retry_budget",
        "abort_requested",
        "pending_tools",
        "tool_results",
        "sandbox_ready",
        "last_tool_error",
    ):
        assert field in STATE_FIELDS
