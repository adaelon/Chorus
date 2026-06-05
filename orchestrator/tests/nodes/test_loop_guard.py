"""S11d loop_guard: read-only routing table."""

from __future__ import annotations

from app.nodes.loop_guard import loop_guard
from app.state import GroupState, ToolCallIntent, ToolResult, ToolRuntimeError


def _intent() -> ToolCallIntent:
    return ToolCallIntent(call_id="call-1", kind="mcp_call", tool_name="search")


def _result() -> ToolResult:
    return ToolResult(call_id="call-1", tool_name="search", ok=True)


def test_loop_guard_routes_pending_tools_to_dispatch():
    assert loop_guard(GroupState(group_key="g", pending_tools=[_intent()])) == "tool_dispatch"


def test_loop_guard_routes_tool_results_back_to_llm_plan():
    assert loop_guard(GroupState(group_key="g", tool_results=[_result()])) == "llm_plan"


def test_loop_guard_routes_sandbox_down_to_degraded_reply():
    assert loop_guard(GroupState(group_key="g", sandbox_ready=False)) == "degraded_reply"


def test_loop_guard_routes_non_sandbox_tool_error_to_human_intervention():
    state = GroupState(
        group_key="g",
        last_tool_error=ToolRuntimeError(code="permission_denied", message="needs approval"),
    )

    assert loop_guard(state) == "human_intervention"


def test_loop_guard_routes_abort_to_done():
    assert loop_guard(GroupState(group_key="g", abort_requested=True)) == "done"
    assert loop_guard(GroupState(group_key="g", run_status="aborted")) == "done"


def test_loop_guard_routes_final_output_to_done():
    assert loop_guard(GroupState(group_key="g", output="done")) == "done"
    assert loop_guard(GroupState(group_key="g", run_status="done")) == "done"


def test_loop_guard_defaults_to_llm_plan():
    assert loop_guard(GroupState(group_key="g")) == "llm_plan"


def test_loop_guard_is_read_only():
    state = GroupState(group_key="g", tool_results=[_result()])
    before = state.model_dump(mode="json")

    route = loop_guard(state)

    assert route == "llm_plan"
    assert state.model_dump(mode="json") == before


def test_loop_guard_route_priority_prefers_closed_abort_and_pending_work():
    assert (
        loop_guard(
            GroupState(
                group_key="g",
                abort_requested=True,
                pending_tools=[_intent()],
                sandbox_ready=False,
            )
        )
        == "done"
    )
    assert (
        loop_guard(
            GroupState(
                group_key="g",
                pending_tools=[_intent()],
                sandbox_ready=False,
            )
        )
        == "tool_dispatch"
    )
