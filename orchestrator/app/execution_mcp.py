"""S12d: MCP adapter for the `mcp_call` intent kind, over the official `mcp` SDK.

`make_mcp_executor` produces a `ToolExecutor` that runs one `mcp_call` intent
against a connected MCP `ClientSession`: `intent.tool_name`/`intent.args` →
`session.call_tool(...)` → `CallToolResult` → `ToolResult`. The session is
supplied by an injectable async-context provider so offline tests mock it; the
real stdio/SSE connection helpers are smoke-verified (`CHORUS_RUN_MCP_SMOKE=1`).

This plugs into `make_real_executor(mcp_executor=...)` (S12a already routes the
`mcp_call` kind there). MCP server *configuration* (hardcoded vs. CRUD registry)
is a separate slice — this one only executes against a given connection.

Verified against `mcp` 1.27.2: `ClientSession` is an async context manager with
async `initialize()`; `await session.call_tool(name, arguments) -> CallToolResult`
with `.content: list[TextContent|...]` (`.text`), `.isError: bool`,
`.structuredContent: dict|None`.

Deferred: MCP server registry/config, connection pooling/reuse.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from .nodes.tool_dispatch import ToolDispatchError, ToolExecutor
from .state import ToolCallIntent, ToolResult, ToolRuntimeError


class McpSession(Protocol):
    """The slice of an MCP `ClientSession` this adapter uses."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...

    async def list_tools(self) -> Any: ...


# A provider opens a connected, initialized MCP session as an async context.
McpSessionProvider = Callable[[], AbstractAsyncContextManager[McpSession]]


def _join_text(content: Any) -> str:
    """Concatenate the text of `TextContent` blocks in a CallToolResult."""
    parts: list[str] = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _to_tool_result(intent: ToolCallIntent, result: Any) -> ToolResult:
    text = _join_text(getattr(result, "content", None))
    data: dict[str, Any] = {}
    structured = getattr(result, "structuredContent", None)
    if structured:
        data["structuredContent"] = structured
    if getattr(result, "isError", False):
        # A tool-level error is a result the LLM should see and react to (it
        # loops back through llm_plan), not an infra failure — return ok=False.
        return ToolResult(
            call_id=intent.call_id,
            tool_name=intent.tool_name,
            ok=False,
            content=text,
            data=data,
            error=ToolRuntimeError(
                code="mcp_tool_error", message=text or "mcp tool returned an error"
            ),
        )
    return ToolResult(
        call_id=intent.call_id,
        tool_name=intent.tool_name,
        ok=True,
        content=text,
        data=data,
    )


def make_mcp_executor(open_session: McpSessionProvider) -> ToolExecutor:
    """Execute an `mcp_call` intent over a session from `open_session`.

    Transport/connection failures are wrapped as `ToolDispatchError` so the
    execution loop routes them (S11c/S11d) instead of crashing the node.
    """

    async def execute(intent: ToolCallIntent) -> ToolResult:
        try:
            async with open_session() as session:
                result = await session.call_tool(intent.tool_name, intent.args or {})
        except ToolDispatchError:
            raise
        except Exception as exc:  # transport/protocol error -> degrade, don't crash
            raise ToolDispatchError(
                "mcp_call_failed", f"mcp call failed: {exc}", retryable=False
            ) from exc
        return _to_tool_result(intent, result)

    return execute


def stdio_mcp_session(
    command: str, args: list[str] | None = None, *, env: dict[str, str] | None = None
) -> McpSessionProvider:
    """Provider that spawns a stdio MCP server and yields an initialized session."""

    @asynccontextmanager
    async def provider() -> AsyncIterator[Any]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=command, args=list(args or []), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    return provider


def sse_mcp_session(url: str, *, headers: dict[str, Any] | None = None) -> McpSessionProvider:
    """Provider that connects to an SSE MCP server and yields an initialized session."""

    @asynccontextmanager
    async def provider() -> AsyncIterator[Any]:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    return provider


# --- registry: many MCP servers -> one tool catalog + a routing executor ----


def provider_for(spec: Any) -> McpSessionProvider:
    """Build a session provider for one McpServer record (duck-typed: transport/…)."""
    if getattr(spec, "transport", "stdio") == "sse":
        return sse_mcp_session(getattr(spec, "url", ""))
    return stdio_mcp_session(getattr(spec, "command", ""), list(getattr(spec, "args", []) or []))


_BUILTIN = "__builtin__"  # sentinel spec for the in-process built-in tools (S14a)


class McpRegistry:
    """Aggregate built-in tools + many MCP servers into one tool catalog for the
    planner + a tool-name routing `ToolExecutor` for `mcp_call` (S13f/S14a).

    `include_builtins` (default True) adds the in-process built-in tools
    (fetch_url / web_search, §6.24) so they're available with zero setup.
    `refresh` connects each server and `list_tools()`; unreachable servers are
    skipped (degrade, don't crash). `provider_factory` is injectable for offline
    tests; defaults to the real stdio/SSE providers.
    """

    def __init__(
        self,
        specs: list[Any],
        *,
        provider_factory=provider_for,
        include_builtins: bool = True,
        builtins: dict | None = None,
    ) -> None:
        self._specs = list(specs)
        self._provider_factory = provider_factory
        self._include_builtins = include_builtins
        self._builtins = builtins  # 内置工具表覆盖（离线测注入假 impl，避免真网络）
        self._tool_to_spec: dict[str, Any] = {}
        self._catalog: list[dict] = []

    def _provider(self, spec: Any) -> McpSessionProvider:
        from .builtin_tools import builtin_provider

        return builtin_provider(self._builtins) if spec == _BUILTIN else self._provider_factory(spec)

    async def _index(self, spec: Any) -> None:
        try:
            async with self._provider(spec)() as session:
                result = await session.list_tools()
        except Exception:  # noqa: BLE001 - unreachable server -> skip
            return
        for tool in getattr(result, "tools", []) or []:
            name = getattr(tool, "name", None)
            if not name:
                continue
            self._tool_to_spec[name] = spec
            self._catalog.append(
                {"name": name, "description": getattr(tool, "description", "") or ""}
            )

    async def refresh(self) -> None:
        self._tool_to_spec = {}
        self._catalog = []
        sources = ([_BUILTIN] if self._include_builtins else []) + self._specs
        for spec in sources:
            await self._index(spec)

    async def reload(self, specs: list) -> None:
        """Replace the server list and re-index tools in-place (hot reload after CRUD)."""
        self._specs = list(specs)
        await self.refresh()

    def catalog(self) -> list[dict]:
        """[{name, description}] across built-ins + all reachable servers (plan prompt)."""
        return list(self._catalog)

    def make_executor(self) -> ToolExecutor:
        """A `ToolExecutor` for `mcp_call`: route by `intent.tool_name` to its source."""

        async def execute(intent: ToolCallIntent) -> ToolResult:
            spec = self._tool_to_spec.get(intent.tool_name)
            if spec is None:
                raise ToolDispatchError(
                    "mcp_unknown_tool",
                    f"no MCP server provides tool {intent.tool_name!r}",
                    retryable=False,
                )
            return await make_mcp_executor(self._provider(spec))(intent)

        return execute
