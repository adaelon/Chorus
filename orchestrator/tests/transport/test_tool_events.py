"""S13c: execution sub-events flow through the runtime as neutral ToolEvents.

`to_event` maps the custom-stream payloads the turn node emits; `iter_events`
(now consuming the custom mode) surfaces them live with speaker/turn
attribution. A minimal graph with a tool-enabled turn exercises the real
stream-writer path.
"""

from __future__ import annotations

import json
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.nodes.turn import turn
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg, ToolResult
from app.transport.runtime import ToolEvent, iter_events, to_event

_TOOL = {
    "kind": "tool_call",
    "call_id": "c1",
    "tool_kind": "sandbox_exec",
    "tool_name": "shell",
    "args": {"command": "python3 -c 'print(55)'"},
    "requires_sandbox": True,
}
_FINAL = {"kind": "final", "text": "done"}


# --- pure mapping -----------------------------------------------------------


def test_to_event_custom_maps_tool_payload():
    ev = to_event(
        "custom",
        {"kind": "tool_call", "speaker_id": "A", "turn": 1, "tool_name": "shell", "command": "ls"},
    )
    assert isinstance(ev, ToolEvent)
    d = ev.to_dict()
    assert d["type"] == "tool_call"
    assert d["speaker_id"] == "A" and d["turn"] == 1 and d["command"] == "ls"


def test_to_event_custom_ignores_non_tool_payload():
    assert to_event("custom", {"kind": "something"}) is None
    assert to_event("custom", "not a dict") is None


# --- integration: tool-enabled turn through iter_events ---------------------


def _plan(scripts):
    n = {"i": 0}

    async def stream(_state):
        i = n["i"]
        n["i"] += 1
        yield json.dumps(scripts[min(i, len(scripts) - 1)])

    return stream


async def _exec_ok(intent):
    return ToolResult(call_id=intent.call_id, tool_name=intent.tool_name, ok=True, content="55")


async def _gen(slot, request, history, claims=None):
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text="speech")


async def _ext(text, speaker_id, turn_idx):
    return [Claim(speaker_id=speaker_id, text="pt", turn=turn_idx)]


def _tool_turn_graph():
    g = StateGraph(GroupState)
    g.add_node(
        "turn",
        partial(turn, plan_stream=_plan([_TOOL, _FINAL]), execute=_exec_ok, generate=_gen, extract=_ext),
    )
    g.add_edge(START, "turn")
    g.add_edge("turn", END)
    return g.compile()


async def test_iter_events_surfaces_tool_subevents_with_attribution():
    graph = _tool_turn_graph()
    state = {
        "group_key": "g",
        "roster": [AgentSlot(contact_id="A", dimension="d")],
        "history": [Msg(sender_id="u", sender_kind="human", text="算 fib(10)")],
        "next_speaker": "A",
    }
    events = [ev async for ev in iter_events(graph, state, {"configurable": {"thread_id": "t"}})]
    tool_events = [e for e in events if isinstance(e, ToolEvent)]

    kinds = [e.payload["kind"] for e in tool_events]
    assert "tool_call" in kinds and "tool_result" in kinds
    # attribution: every tool sub-event carries the speaker + turn
    assert all(e.payload["speaker_id"] == "A" and e.payload["turn"] == 1 for e in tool_events)
    call = next(e.to_dict() for e in tool_events if e.payload["kind"] == "tool_call")
    assert call["tool_name"] == "shell"
    result = next(e.to_dict() for e in tool_events if e.payload["kind"] == "tool_result")
    assert result["ok"] is True and result["content"] == "55"
