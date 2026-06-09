"""S13b: turn weaves the execution loop (β + gating, §6.24).

A tool-enabled turn (plan_stream + execute injected) runs a ReAct tool phase,
then the existing streaming generate produces the speech with the tool findings
in context — claims/history unchanged. Without injection the turn behaves
exactly as today (gating / A3).
"""

from __future__ import annotations

import json

from app.nodes.turn import find_turn_trace, turn
from app.state import AgentSlot, Candidate, Claim, GroupState, Msg, ToolResult

_TOOL = {
    "kind": "tool_call",
    "call_id": "c1",
    "tool_kind": "sandbox_exec",
    "tool_name": "shell",
    "args": {"command": "python3 -c 'print(55)'"},
    "requires_sandbox": True,
}
_FINAL = {"kind": "final", "text": "done"}


def _plan(scripts):
    n = {"i": 0}

    async def stream(_state):
        i = n["i"]
        n["i"] += 1
        yield json.dumps(scripts[min(i, len(scripts) - 1)])

    return stream


async def _exec_ok(intent):
    return ToolResult(call_id=intent.call_id, tool_name=intent.tool_name, ok=True, content="55")


def _capture_gen(box):
    async def gen(slot, request, history, claims=None):
        box["request"] = request
        return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text="speech")

    return gen


async def _ext(text, speaker_id, turn_idx):
    return [Claim(speaker_id=speaker_id, text="pt", turn=turn_idx)]


def _state() -> GroupState:
    return GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id="A", dimension="d")],
        history=[Msg(sender_id="u", sender_kind="human", text="算 fib(10)")],
        next_speaker="A",
    )


async def test_tool_enabled_turn_feeds_tool_findings_into_speech():
    box: dict = {}
    out = await turn(
        _state(),
        generate=_capture_gen(box),
        extract=_ext,
        plan_stream=_plan([_TOOL, _FINAL]),
        execute=_exec_ok,
    )
    # the tool result reached the speech prompt (β: speech via generate, not final.text)
    assert "55" in box["request"]
    assert "fib(10)" in box["request"]
    # normal turn outputs unchanged
    assert out["history"][-1].text == "speech"
    assert out["history"][-1].sender_kind == "ai"
    assert out["turns_since_human"] == 1
    assert len(out["claims"]) == 1


async def test_planner_uses_no_tools_leaves_request_unaugmented():
    box: dict = {}
    await turn(
        _state(),
        generate=_capture_gen(box),
        extract=_ext,
        plan_stream=_plan([_FINAL]),  # straight to final, no tool_call
        execute=_exec_ok,
    )
    assert "fib(10)" in box["request"]
    assert "已执行的工具调用" not in box["request"]


async def test_gating_without_execution_is_todays_behavior():
    box: dict = {}
    out = await turn(_state(), generate=_capture_gen(box), extract=_ext)
    # no tool phase: request is the plain topic, no tool context appended
    assert box["request"] == "算 fib(10)"
    assert out["history"][-1].text == "speech"
    assert out["turns_since_human"] == 1
    assert "turn_traces" not in out


async def test_tool_enabled_turn_stores_trace_by_speaker_and_turn():
    out = await turn(
        _state(),
        generate=_capture_gen({}),
        extract=_ext,
        plan_stream=_plan([_TOOL, _FINAL]),
        execute=_exec_ok,
    )
    traces = out["turn_traces"]
    assert len(traces) == 1
    tr = traces[0]
    assert tr.speaker_id == "A" and tr.turn == 1
    assert tr.steps[0].args["command"]  # the command is retained
    # retrieval helper
    state2 = _state().model_copy(update={"turn_traces": traces})
    assert find_turn_trace(state2, "A", 1) is tr
    assert find_turn_trace(state2, "A", 99) is None


async def test_repeated_intent_stops_tool_phase():
    """planner 反复发同一个 tool_call → 去重挡住，工具阶段停止，不空转到 max_tool_steps（S16a/§6.27 C）。"""
    out = await turn(
        _state(),
        generate=_capture_gen({}),
        extract=_ext,
        plan_stream=_plan([_TOOL]),  # 永远发同一个 _TOOL、从不 final（模拟原地打转）
        execute=_exec_ok,
        max_tool_steps=6,
    )
    # 去重：同 (tool_name, args) 只执行一次，而非 6 次空转
    tr = out["turn_traces"][0]
    assert len(tr.steps) == 1


async def test_tool_gate_false_skips_tool_phase():
    """准入门返回 False（不需要工具）→ 跳过工具阶段，request 不增、无 trace（S16b/§6.27 A）。"""
    box: dict = {}

    async def gate_no(_state):
        return False

    out = await turn(
        _state(),
        generate=_capture_gen(box),
        extract=_ext,
        plan_stream=_plan([_TOOL, _FINAL]),  # 即便 planner 想用工具
        execute=_exec_ok,
        tool_gate=gate_no,
    )
    assert box["request"] == "算 fib(10)"  # 纯议题，没跑工具阶段
    assert "turn_traces" not in out


async def test_tool_gate_true_runs_tool_phase():
    """准入门返回 True → 仍跑工具阶段（门控另一侧，S16b）。"""
    box: dict = {}

    async def gate_yes(_state):
        return True

    await turn(
        _state(),
        generate=_capture_gen(box),
        extract=_ext,
        plan_stream=_plan([_TOOL, _FINAL]),
        execute=_exec_ok,
        tool_gate=gate_yes,
    )
    assert "55" in box["request"]  # 工具结果进了发言 prompt


async def test_no_tools_used_stores_no_trace():
    out = await turn(
        _state(),
        generate=_capture_gen({}),
        extract=_ext,
        plan_stream=_plan([_FINAL]),  # planner uses no tools
        execute=_exec_ok,
    )
    assert "turn_traces" not in out
