"""S11e: minimal self-contained execution loop for contract verification."""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .nodes.llm_plan import PlanStream, llm_plan
from .nodes.loop_guard import loop_guard
from .nodes.tool_dispatch import ToolExecutor, tool_dispatch
from .state import GroupState, TraceEvent


async def degraded_reply(state: GroupState) -> dict:
    """P0 degraded terminal node for sandbox/tool unavailability."""
    trace = list(state.trace_events)
    trace.append(TraceEvent(node="degraded_reply", status="closed"))
    return {
        "output": "沙箱暂时不可用，请稍后重试或等待人工处理。",
        "run_status": "degraded",
        "trace_events": trace,
    }


async def human_intervention(state: GroupState) -> dict:
    """P0 terminal node for errors that need manual handling."""
    trace = list(state.trace_events)
    trace.append(TraceEvent(node="human_intervention", status="closed"))
    return {
        "output": "工具执行需要人工处理。",
        "run_status": "degraded",
        "trace_events": trace,
    }


def build_execution_loop(
    checkpointer=None,
    *,
    stream: PlanStream,
    execute: ToolExecutor,
):
    """Build the S11 P0 loop: llm_plan -> guard -> dispatch/degrade/done."""
    g = StateGraph(GroupState)
    g.add_node("llm_plan", partial(llm_plan, stream=stream))
    g.add_node("tool_dispatch", partial(tool_dispatch, execute=execute))
    g.add_node("degraded_reply", degraded_reply)
    g.add_node("human_intervention", human_intervention)
    g.add_edge(START, "llm_plan")
    g.add_conditional_edges(
        "llm_plan",
        loop_guard,
        {
            "tool_dispatch": "tool_dispatch",
            "llm_plan": "llm_plan",
            "degraded_reply": "degraded_reply",
            "human_intervention": "human_intervention",
            "done": END,
        },
    )
    g.add_conditional_edges(
        "tool_dispatch",
        loop_guard,
        {
            "tool_dispatch": "tool_dispatch",
            "llm_plan": "llm_plan",
            "degraded_reply": "degraded_reply",
            "human_intervention": "human_intervention",
            "done": END,
        },
    )
    g.add_edge("degraded_reply", END)
    g.add_edge("human_intervention", END)
    return g.compile(checkpointer=checkpointer)
