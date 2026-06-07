"""S13a: a real planner stream for the execution loop (§6.24, sandbox-only).

`llm_plan` consumes a `PlanStream = (GroupState) -> AsyncIterable[str]` and
parses the collected text as the narrow JSON intent protocol (S11b). This
builds the real LLM-backed stream: a ReAct prompt (agent role + strict JSON
protocol + sandbox tool + scratchpad of prior tool calls/results) fed to the
model, which emits the next `tool_call` or `final` JSON.

Structured output goes through `robust_ainvoke` (astream + retry, §6.9/kimi
reliability); `llm_plan` then extracts the JSON robustly (reasoning prefixes /
```json fences tolerated). MCP tools join the catalog in S13f; here the only
tool is `sandbox_exec`.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import robust_ainvoke
from ..state import AgentStep, GroupState
from ._common import request_text

PlanStream = Callable[[GroupState], AsyncIterable[str]]

_PLAN_BASE = """你是一个能用工具干活的助手。每一步**只输出一个 JSON 对象**，\
不要任何额外文字、解释或 ``` 围栏。

要在沙箱里运行命令/代码时，输出：
{"kind":"tool_call","call_id":"<唯一字符串>","tool_kind":"sandbox_exec","tool_name":"shell",\
"args":{"command":"<要执行的 shell 命令>"},"requires_sandbox":true}
"""

_PLAN_MCP = """要调用一个 MCP 工具时，输出：
{"kind":"tool_call","call_id":"<唯一字符串>","tool_kind":"mcp_call","tool_name":"<工具名>",\
"args":{<该工具的参数>}}
"""

_PLAN_FINAL = """已经得到最终答案时，输出：
{"kind":"final","text":"<给用户的最终答案>"}

规则：一次只发一个 JSON；用工具后看到结果再决定下一步；信息足够就给 final。"""


def _plan_system(tool_catalog: list[dict] | None) -> str:
    """组装 planner 系统提示：sandbox_exec + final 永远在；有 MCP 目录则列出可用工具（S13f）。"""
    parts = [_PLAN_BASE]
    tools = ["- sandbox_exec：在隔离沙箱里执行一条 shell 命令（args.command）。跑 Python 用 `python3 -c \"...\"`。"]
    if tool_catalog:
        parts.append(_PLAN_MCP)
        for t in tool_catalog:
            desc = (t.get("description") or "").strip().replace("\n", " ")
            tools.append(f"- {t['name']}（mcp_call）：{desc}")
    parts.append("可用工具：\n" + "\n".join(tools) + "\n")
    parts.append(_PLAN_FINAL)
    return "\n".join(parts)


def _render_scratchpad(steps: list[AgentStep]) -> str:
    """把历次工具调用+结果渲染成文本（planner 据此知道'我跑了啥、得到啥'）。"""
    if not steps:
        return ""
    lines = ["", "已执行的工具调用与结果："]
    for i, step in enumerate(steps, 1):
        command = step.args.get("command", "") if isinstance(step.args, dict) else ""
        status = "ok" if step.ok else f"error({step.error})"
        output = (step.content or "").strip()
        lines.append(f"{i}. sandbox_exec command={command!r} -> {status}\n输出：{output}")
    return "\n".join(lines)


def _build_plan_messages(state: GroupState, tool_catalog: list[dict] | None = None) -> list:
    task = request_text(state)
    user = (
        f"任务：{task}"
        f"{_render_scratchpad(state.agent_steps)}"
        "\n\n请决定下一步（只输出一个 JSON 对象）。"
    )
    return [SystemMessage(content=_plan_system(tool_catalog)), HumanMessage(content=user)]


def default_plan_stream(model, *, tool_catalog: list[dict] | None = None) -> PlanStream:
    """Real planner: build the ReAct prompt (sandbox + any MCP tools), ask the model.

    `tool_catalog` (from `McpRegistry.catalog()`, S13f) adds MCP tools to the
    prompt so the planner can emit `mcp_call` intents.
    """

    async def stream(state: GroupState) -> AsyncIterable[str]:
        msg = await robust_ainvoke(model, _build_plan_messages(state, tool_catalog))
        yield str(msg.content or "")

    return stream
