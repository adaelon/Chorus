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

from collections.abc import AsyncIterable, Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import robust_ainvoke
from ..state import AgentStep, GroupState
from ._common import request_text

PlanStream = Callable[[GroupState], AsyncIterable[str]]
ToolGate = Callable[[GroupState], Awaitable[bool]]

_PLAN_INTRO = """你是一个能用工具干活的助手。每一步**只输出一个 JSON 对象**，\
不要任何额外文字、解释或 ``` 围栏。
"""

_PLAN_SANDBOX = """要在隔离沙箱里运行命令/代码时，输出：
{"kind":"tool_call","call_id":"<唯一字符串>","tool_kind":"sandbox_exec","tool_name":"shell",\
"args":{"command":"<要执行的 shell 命令>"},"requires_sandbox":true}
"""

_PLAN_MCP = """要调用一个工具时，输出：
{"kind":"tool_call","call_id":"<唯一字符串>","tool_kind":"mcp_call","tool_name":"<工具名>",\
"args":{<该工具的参数>}}
"""

_PLAN_FINAL = """已经得到最终答案时，输出：
{"kind":"final","text":"<给用户的最终答案>"}

规则：一次只发一个 JSON；用工具后看到结果再决定下一步；信息足够就给 final。\
**若这个问题不需要任何工具（闲聊、观点、常识、寒暄等），第一步就直接给 final，不要为探索而调用工具。**\
不要重复已经调用过的工具（同样的工具+参数不要再发一遍）。"""


def _plan_system(tool_catalog: list[dict] | None, *, has_sandbox: bool = True) -> str:
    """组装 planner 系统提示：按可用工具列出 sandbox_exec（有沙箱才列）+ 各 MCP/内置工具（S13f/S14a）。"""
    parts = [_PLAN_INTRO]
    tools: list[str] = []
    if has_sandbox:
        parts.append(_PLAN_SANDBOX)
        tools.append('- sandbox_exec：在隔离沙箱里执行一条 shell 命令（args.command）。跑 Python 用 `python3 -c "..."`。')
    if tool_catalog:
        parts.append(_PLAN_MCP)
        for t in tool_catalog:
            desc = (t.get("description") or "").strip().replace("\n", " ")
            tools.append(f"- {t['name']}（mcp_call）：{desc}")
    parts.append("可用工具：\n" + "\n".join(tools) + "\n")
    parts.append(_PLAN_FINAL)
    return "\n".join(parts)


def _render_scratchpad(steps: list[AgentStep]) -> str:
    """把历次工具调用+结果渲染成文本（planner 据此知道'我跑了哪个工具、传了啥参、得到啥'）。

    用真实 `tool_name` + 真实 args（S16a/§6.27 C）——此前硬编码 `sandbox_exec command=`
    对 MCP 调用渲成 `command='' -> ok`，planner 看不出已调过哪个工具/路径 → 原地打转。
    """
    if not steps:
        return ""
    lines = ["", "已执行的工具调用与结果："]
    for i, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else {}
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        status = "ok" if step.ok else f"error({step.error})"
        output = (step.content or "").strip()
        lines.append(f"{i}. {step.tool_name}({args_str}) -> {status}\n输出：{output}")
    return "\n".join(lines)


def _build_plan_messages(
    state: GroupState, tool_catalog: list[dict] | None = None, *, has_sandbox: bool = True
) -> list:
    task = request_text(state)
    user = (
        f"任务：{task}"
        f"{_render_scratchpad(state.agent_steps)}"
        "\n\n请决定下一步（只输出一个 JSON 对象）。"
    )
    return [
        SystemMessage(content=_plan_system(tool_catalog, has_sandbox=has_sandbox)),
        HumanMessage(content=user),
    ]


def default_plan_stream(
    model, *, tool_catalog=None, has_sandbox: bool = True
) -> PlanStream:
    """Real planner: build the ReAct prompt (sandbox + MCP/built-in tools), ask the model.

    `tool_catalog` may be a `list[dict]` (static) or a callable `() -> list[dict]`
    (e.g. `McpRegistry.catalog` bound method) evaluated fresh on every invocation,
    so hot-reloading the registry is reflected without restarting the server.
    `has_sandbox=False` drops sandbox_exec from the prompt (S14a).
    """

    async def stream(state: GroupState) -> AsyncIterable[str]:
        catalog = tool_catalog() if callable(tool_catalog) else tool_catalog
        msg = await robust_ainvoke(
            model, _build_plan_messages(state, catalog, has_sandbox=has_sandbox)
        )
        yield str(msg.content or "")

    return stream


_GATE_SYSTEM = """你是一个判定器。下面是群聊里要某位成员回应的一条消息/任务。\
判断：回应它是否**需要调用工具**（运行代码/计算、读写文件、联网搜索、查询外部数据等）才能完成？
闲聊、寒暄、表达观点、常识问答、基于已有上下文的讨论都**不需要**工具。
只回答两个字之一：需要 或 不需要。"""


def default_tool_gate(model) -> ToolGate:
    """廉价单一职责准入门：判定这轮发言需不需要工具（S16b/§6.27 A，治本）。

    单目的 yes/no prompt 比满载 planner 可靠。**保守默认**——只有模型明确说"不需要/无需/否"
    才返回 False（跳过工具阶段）；"需要"/模糊/空一律 True（仍跑工具，宁可多跑不误杀；
    跑起来的空转由 S16a scratchpad 修复+去重兜底）。
    """

    async def gate(state: GroupState) -> bool:
        task = request_text(state)
        msg = await robust_ainvoke(
            model, [SystemMessage(content=_GATE_SYSTEM), HumanMessage(content=f"消息：{task}")]
        )
        text = str(msg.content or "").strip()
        if "不需要" in text or "无需" in text or text.startswith("否") or text.lower().startswith("no"):
            return False
        return True

    return gate
