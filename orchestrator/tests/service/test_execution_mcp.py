"""S12d: MCP adapter over a mocked ClientSession (offline) + gated smoke.

Offline tests inject a fake session shaped like mcp's `ClientSession`
(`call_tool` -> `CallToolResult` with `.content`/`.isError`/`.structuredContent`);
they lock the result translation. The real stdio/SSE path is smoke-verified
(`CHORUS_RUN_MCP_SMOKE=1`). The adapter targets the SDK, so skip if absent.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")

from app.execution_mcp import (  # noqa: E402
    make_mcp_executor,
    stdio_mcp_session,
)
from app.execution_sandbox import make_real_executor  # noqa: E402
from app.nodes.tool_dispatch import ToolDispatchError  # noqa: E402
from app.state import ToolCallIntent  # noqa: E402


class _Text:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Result:
    def __init__(self, content=None, *, is_error: bool = False, structured=None) -> None:
        self.content = content or []
        self.isError = is_error
        self.structuredContent = structured


class _FakeSession:
    def __init__(self, result=None, *, raise_exc: Exception | None = None) -> None:
        self._result = result
        self._raise = raise_exc
        self.calls: list[tuple] = []

    async def call_tool(self, name: str, arguments=None):
        self.calls.append((name, arguments))
        if self._raise is not None:
            raise self._raise
        return self._result


def _provider(session: _FakeSession):
    @asynccontextmanager
    async def open_session():
        yield session

    return open_session


def _intent(**kw) -> ToolCallIntent:
    data = {"call_id": "c1", "kind": "mcp_call", "tool_name": "search"}
    data.update(kw)
    return ToolCallIntent(**data)


async def test_mcp_call_success_joins_text_and_passes_args():
    session = _FakeSession(_Result([_Text("hel"), _Text("lo")], structured={"k": 1}))
    execute = make_mcp_executor(_provider(session))

    result = await execute(_intent(args={"q": "x"}))

    assert session.calls == [("search", {"q": "x"})]
    assert result.ok is True
    assert result.content == "hello"
    assert result.data["structuredContent"] == {"k": 1}


async def test_mcp_call_empty_args_passed_as_none():
    session = _FakeSession(_Result([_Text("ok")]))
    execute = make_mcp_executor(_provider(session))

    await execute(_intent(args={}))

    assert session.calls == [("search", None)]


async def test_mcp_tool_error_returns_ok_false_with_error():
    session = _FakeSession(_Result([_Text("boom")], is_error=True))
    execute = make_mcp_executor(_provider(session))

    result = await execute(_intent())

    assert result.ok is False
    assert result.content == "boom"
    assert result.error is not None
    assert result.error.code == "mcp_tool_error"


async def test_transport_exception_wrapped_as_dispatch_error():
    session = _FakeSession(raise_exc=RuntimeError("connection reset"))
    execute = make_mcp_executor(_provider(session))

    with pytest.raises(ToolDispatchError) as excinfo:
        await execute(_intent())

    assert excinfo.value.error.code == "mcp_call_failed"
    assert excinfo.value.error.retryable is False


async def test_make_real_executor_routes_mcp_kind_to_mcp_executor():
    session = _FakeSession(_Result([_Text("routed")]))
    execute = make_real_executor(mcp_executor=make_mcp_executor(_provider(session)))

    result = await execute(_intent(tool_name="search", args={"q": "y"}))

    assert result.content == "routed"
    assert session.calls == [("search", {"q": "y"})]


async def test_stdio_provider_is_constructible_without_connecting():
    # Building the provider must not spawn a process; connection is lazy.
    provider = stdio_mcp_session("python", ["-m", "server"], env={"K": "v"})
    assert callable(provider)


@pytest.mark.skipif(
    os.getenv("CHORUS_RUN_MCP_SMOKE") != "1",
    reason="set CHORUS_RUN_MCP_SMOKE=1 with CHORUS_MCP_COMMAND/ARGS/TOOL to smoke",
)
async def test_smoke_real_mcp_server():
    import json

    command = os.environ["CHORUS_MCP_COMMAND"]
    args = os.getenv("CHORUS_MCP_ARGS", "").split()
    tool = os.environ["CHORUS_MCP_TOOL"]
    tool_args = json.loads(os.getenv("CHORUS_MCP_ARGS_JSON", "{}"))

    execute = make_mcp_executor(stdio_mcp_session(command, args))
    result = await execute(_intent(tool_name=tool, args=tool_args))

    assert result.ok is True
