"""S11d: pure router for the execution subgraph loop.

The guard is intentionally read-only: it returns an edge label and never emits a
state delta. Error/degradation facts must already be present in GroupState.
"""

from __future__ import annotations

from typing import Literal

from ..state import GroupState

LoopRoute = Literal[
    "tool_dispatch",
    "llm_plan",
    "degraded_reply",
    "human_intervention",
    "done",
]


def loop_guard(state: GroupState) -> LoopRoute:
    """Route the execution loop from closed state facts."""
    if state.abort_requested or state.run_status in {"aborted", "done"}:
        return "done"
    if state.pending_tools:
        return "tool_dispatch"
    if state.sandbox_ready is False:
        return "degraded_reply"
    if state.last_tool_error is not None:
        return "human_intervention"
    if state.tool_results:
        return "llm_plan"
    if state.output is not None:
        return "done"
    return "llm_plan"
