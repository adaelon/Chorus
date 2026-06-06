"""S12a: sandbox protocol translation, kind dispatch, and offline FakeBackend."""

from __future__ import annotations

import pytest

from app.execution_sandbox import FakeBackend, make_real_executor, make_sandbox_executor
from app.nodes.tool_dispatch import ToolDispatchError
from app.state import ToolCallIntent, ToolResult


def _intent(**kw) -> ToolCallIntent:
    data = {"call_id": "call-1", "kind": "sandbox_exec", "tool_name": "python"}
    data.update(kw)
    return ToolCallIntent(**data)


async def test_sandbox_exec_success_returns_ok_result():
    backend = FakeBackend(stdout="hello\n")
    execute = make_sandbox_executor(backend)

    result = await execute(_intent(args={"command": "echo hello"}))

    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.content == "hello\n"
    assert result.data["exit_code"] == 0
    # one fresh session opened and closed (S12a: no reuse)
    assert len(backend.sessions) == 1
    assert backend.sessions[0].closed is True


async def test_sandbox_exec_nonzero_exit_raises_dispatch_error():
    backend = FakeBackend(exit_code=2, stderr="boom")
    execute = make_sandbox_executor(backend)

    with pytest.raises(ToolDispatchError) as excinfo:
        await execute(_intent(args={"command": "false"}))

    assert excinfo.value.error.code == "sandbox_exec_failed"
    assert excinfo.value.error.retryable is False
    # session still closed even on failure
    assert backend.sessions[0].closed is True


async def test_sandbox_skill_writes_files_before_running():
    backend = FakeBackend()
    execute = make_sandbox_executor(backend)

    await execute(
        _intent(
            kind="sandbox_skill",
            args={"files": {"/workspace/skills/x/SKILL.md": "# x"}, "command": "run x"},
        )
    )

    session = backend.sessions[0]
    assert session.events == ["write_files", "run", "close"]
    assert session.fs["/workspace/skills/x/SKILL.md"] == "# x"


async def test_sandbox_exec_without_files_skips_write():
    backend = FakeBackend()
    execute = make_sandbox_executor(backend)

    await execute(_intent(args={"command": "ls"}))

    assert backend.sessions[0].events == ["run", "close"]


async def test_real_executor_dispatches_sandbox_kinds_to_backend():
    backend = FakeBackend(stdout="ran")
    execute = make_real_executor(sandbox_backend=backend)

    result = await execute(_intent(kind="sandbox_exec", args={"command": "ls"}))

    assert result.ok is True
    assert result.content == "ran"
    assert len(backend.sessions) == 1


async def test_real_executor_mcp_without_executor_raises_not_implemented():
    execute = make_real_executor(sandbox_backend=FakeBackend())

    with pytest.raises(NotImplementedError):
        await execute(_intent(kind="mcp_call", tool_name="search"))


async def test_real_executor_mcp_delegates_to_injected_executor():
    async def fake_mcp(intent: ToolCallIntent) -> ToolResult:
        return ToolResult(call_id=intent.call_id, tool_name=intent.tool_name, ok=True, content="mcp")

    execute = make_real_executor(sandbox_backend=FakeBackend(), mcp_executor=fake_mcp)

    result = await execute(_intent(kind="mcp_call", tool_name="search"))

    assert result.content == "mcp"


async def test_real_executor_sandbox_kind_without_backend_degrades():
    execute = make_real_executor()

    with pytest.raises(ToolDispatchError) as excinfo:
        await execute(_intent(kind="sandbox_exec", args={"command": "ls"}))

    assert excinfo.value.error.code == "sandbox_unavailable"
    assert excinfo.value.sandbox_ready is False
