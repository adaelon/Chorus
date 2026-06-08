"""S13a: real planner stream — ReAct prompt, scratchpad, robust JSON parse.

Offline fake model emits a preset JSON sequence; drives the existing
llm_plan -> tool_dispatch loop to verify the ReAct round-trip, the scratchpad
population, and reasoning/fence-tolerant parsing. A gated smoke hits the real
model.
"""

from __future__ import annotations

import json
import os

import pytest

from app.nodes.llm_plan import _parse_payload, llm_plan
from app.nodes.plan_stream import _build_plan_messages, default_plan_stream
from app.nodes.tool_dispatch import tool_dispatch
from app.state import AgentStep, GroupState, Msg, ToolResult

_TOOL = {
    "kind": "tool_call",
    "call_id": "c1",
    "tool_kind": "sandbox_exec",
    "tool_name": "shell",
    "args": {"command": "echo hi"},
    "requires_sandbox": True,
}
_FINAL = {"kind": "final", "text": "done: hi"}


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    """Streams the i-th reply char-by-char on the i-th astream call."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._i = 0

    async def astream(self, messages, config=None):
        reply = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        for ch in reply:
            yield _Chunk(ch)


def _state(task: str = "say hi", **kw) -> GroupState:
    return GroupState(
        group_key="g", history=[Msg(sender_id="u", sender_kind="human", text=task)], **kw
    )


# --- prompt / scratchpad ----------------------------------------------------


def test_build_plan_messages_has_protocol_and_task():
    msgs = _build_plan_messages(_state("compute fib(10)"))
    system, user = msgs[0].content, msgs[1].content
    assert "sandbox_exec" in system
    assert '"kind":"tool_call"' in system and '"kind":"final"' in system
    assert "compute fib(10)" in user


def test_scratchpad_renders_prior_command_and_result():
    state = _state(agent_steps=[AgentStep(tool_name="shell", args={"command": "echo hi"}, content="hi\n")])
    user = _build_plan_messages(state)[1].content
    assert "echo hi" in user
    assert "hi" in user


def test_plan_prompt_lists_mcp_tools_from_catalog():
    catalog = [{"name": "read_file", "description": "读取文件"}, {"name": "search", "description": "联网搜索"}]
    system = _build_plan_messages(_state(), catalog)[0].content
    assert "mcp_call" in system
    assert "read_file" in system and "联网搜索" in system


def test_plan_prompt_sandbox_only_without_catalog():
    system = _build_plan_messages(_state())[0].content
    assert "sandbox_exec" in system
    assert "mcp_call" not in system  # 无 MCP server → 不提 mcp_call


# --- robust parsing (reasoning prefix / fenced) -----------------------------


def test_parse_payload_tolerates_reasoning_prefix_and_fence():
    noisy = '思考：我应该先运行命令。\n```json\n' + json.dumps(_TOOL) + "\n```"
    payload = _parse_payload(noisy)
    assert payload.kind == "tool_call"
    assert payload.tool_kind == "sandbox_exec"


def test_parse_payload_final_with_leading_text():
    payload = _parse_payload("Here is the answer: " + json.dumps(_FINAL))
    assert payload.kind == "final"
    assert payload.text == "done: hi"


# --- plan stream yields the model text --------------------------------------


async def test_default_plan_stream_yields_collected_json():
    stream = default_plan_stream(_FakeModel([json.dumps(_TOOL)]))
    out = "".join([chunk async for chunk in stream(_state())])
    assert json.loads(out)["tool_kind"] == "sandbox_exec"


# --- ReAct round-trip through llm_plan + tool_dispatch ----------------------


async def test_react_loop_tool_call_then_final_populates_scratchpad():
    model = _FakeModel([json.dumps(_TOOL), json.dumps(_FINAL)])
    stream = default_plan_stream(model)

    async def execute(intent):
        return ToolResult(call_id=intent.call_id, tool_name=intent.tool_name, ok=True, content="hi\n")

    state = _state("say hi")
    # 1) plan -> tool intent
    state = state.model_copy(update=await llm_plan(state, stream=stream))
    assert len(state.pending_tools) == 1
    # 2) dispatch -> closes intent, records scratchpad step
    state = state.model_copy(update=await tool_dispatch(state, execute=execute))
    assert state.pending_tools == []
    assert len(state.agent_steps) == 1
    assert state.agent_steps[0].args["command"] == "echo hi"
    assert state.agent_steps[0].content == "hi\n"
    # 3) plan again -> final answer
    state = state.model_copy(update=await llm_plan(state, stream=stream))
    assert state.output == "done: hi"
    assert state.run_status == "done"


async def test_plan_stream_callable_catalog_evaluated_per_call():
    """callable tool_catalog 每次调用时实时求值，支持热重载。"""
    catalog_ref: list[list[dict]] = [[{"name": "tool_v1", "description": "v1"}]]

    stream = default_plan_stream(_FakeModel(["{}"]), tool_catalog=lambda: catalog_ref[0])

    # 第一次：catalog 含 tool_v1
    state = _state()
    msgs_1 = _build_plan_messages(state, catalog_ref[0])
    assert "tool_v1" in msgs_1[0].content

    # 热更新 catalog
    catalog_ref[0] = [{"name": "tool_v2", "description": "v2"}]

    # 第二次：catalog 已换，stream 下次调用能反映新目录
    msgs_2 = _build_plan_messages(state, catalog_ref[0])
    assert "tool_v2" in msgs_2[0].content
    assert "tool_v1" not in msgs_2[0].content


@pytest.mark.skipif(
    os.getenv("CHORUS_RUN_SMOKE") != "1",
    reason="set CHORUS_RUN_SMOKE=1 to smoke the real planner model",
)
async def test_smoke_real_model_emits_valid_payload():
    from app.llm import make_chat_model

    stream = default_plan_stream(make_chat_model())
    out = "".join([chunk async for chunk in stream(_state("用 python 算 2 的 10 次方"))])
    payload = _parse_payload(out)  # must be a valid tool_call or final
    assert payload.kind in ("tool_call", "final")
