"""S12b: OpenSandboxBackend translation over async SDK-shaped fakes + smoke.

The real `opensandbox` SDK is async-native (0.1.9). These fakes mirror its
async shapes (`commands.run`, `files.write_files`/`read_file`, `kill`,
`logs.stdout` as a list of `.text` messages); offline tests lock the
translation, the gated smoke test (`CHORUS_RUN_SANDBOX_SMOKE=1`) hits a real
server. The adapter targets the SDK, so skip cleanly if it is not installed.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("opensandbox")

from app.execution_opensandbox import OpenSandboxBackend, OpenSandboxSession  # noqa: E402
from app.execution_sandbox import make_sandbox_executor  # noqa: E402
from app.nodes.tool_dispatch import ToolDispatchError  # noqa: E402
from app.state import ToolCallIntent  # noqa: E402


class _Msg:
    """Stand-in for opensandbox OutputMessage (has `.text`)."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeLogs:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = [_Msg(stdout)] if stdout else []
        self.stderr = [_Msg(stderr)] if stderr else []


class _FakeExec:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.logs = _FakeLogs(stdout, stderr)
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self, result: _FakeExec) -> None:
        self._result = result
        self.calls: list[str] = []

    async def run(self, command: str, **_kw) -> _FakeExec:
        self.calls.append(command)
        return self._result


class _FakeFiles:
    def __init__(self) -> None:
        self.written: list = []
        self.store: dict[str, str] = {}

    async def write_files(self, entries: list) -> None:
        self.written.extend(entries)
        for entry in entries:
            self.store[entry.path] = entry.data

    async def read_file(self, path: str, **_kw) -> str:
        return self.store[path]


class _FakeSandbox:
    def __init__(self, result: _FakeExec | None = None) -> None:
        self.id = "sbx-1"
        self.commands = _FakeCommands(result or _FakeExec(stdout="hi\n"))
        self.files = _FakeFiles()
        self.killed = False

    async def kill(self) -> None:
        self.killed = True


def _afactory(sandbox: _FakeSandbox):
    async def factory() -> _FakeSandbox:
        return sandbox

    return factory


def _ready(value: bool):
    async def probe() -> bool:
        return value

    return probe


def _intent(**kw) -> ToolCallIntent:
    data = {"call_id": "c1", "kind": "sandbox_exec", "tool_name": "python"}
    data.update(kw)
    return ToolCallIntent(**data)


async def test_open_wraps_sandbox_and_exposes_session_id():
    sandbox = _FakeSandbox()
    backend = OpenSandboxBackend(factory=_afactory(sandbox))

    session = await backend.open()

    assert isinstance(session, OpenSandboxSession)
    assert session.session_id == "sbx-1"


async def test_run_joins_output_messages_and_reads_exit_code():
    sandbox = _FakeSandbox(_FakeExec(stdout="out\n", stderr="err", exit_code=0))
    session = OpenSandboxSession(sandbox)

    result = await session.run("echo out")

    assert sandbox.commands.calls == ["echo out"]
    assert result.stdout == "out\n"  # joined from list[OutputMessage]
    assert result.stderr == "err"
    assert result.exit_code == 0


async def test_write_files_builds_write_entries_from_mapping():
    sandbox = _FakeSandbox()
    session = OpenSandboxSession(sandbox)

    await session.write_files({"/workspace/a.py": "print(1)"})

    assert len(sandbox.files.written) == 1
    entry = sandbox.files.written[0]
    assert entry.path == "/workspace/a.py"  # real opensandbox WriteEntry
    assert entry.data == "print(1)"
    assert await session.read_file("/workspace/a.py") == "print(1)"


async def test_close_kills_sandbox():
    sandbox = _FakeSandbox()
    session = OpenSandboxSession(sandbox)

    await session.close()

    assert sandbox.killed is True


async def test_readiness_uses_injected_probe():
    backend = OpenSandboxBackend(factory=_afactory(_FakeSandbox()), readiness_probe=_ready(False))
    assert await backend.readiness() is False


async def test_reconnect_not_implemented_until_s12c():
    backend = OpenSandboxBackend(factory=_afactory(_FakeSandbox()))
    with pytest.raises(NotImplementedError):
        await backend.reconnect("sbx-1")


async def test_backend_drives_sandbox_executor_end_to_end():
    """S12a executor over the S12b backend: skill writes then runs, then kills."""
    sandbox = _FakeSandbox(_FakeExec(stdout="done\n", exit_code=0))
    backend = OpenSandboxBackend(factory=_afactory(sandbox))
    execute = make_sandbox_executor(backend)

    result = await execute(
        _intent(kind="sandbox_skill", args={"files": {"/s/SKILL.md": "# s"}, "command": "go"})
    )

    assert result.ok is True
    assert result.content == "done\n"
    assert sandbox.files.written[0].path == "/s/SKILL.md"
    assert sandbox.commands.calls == ["go"]
    assert sandbox.killed is True


async def test_nonzero_exit_surfaces_as_dispatch_error():
    sandbox = _FakeSandbox(_FakeExec(stderr="nope", exit_code=1))
    backend = OpenSandboxBackend(factory=_afactory(sandbox))
    execute = make_sandbox_executor(backend)

    with pytest.raises(ToolDispatchError) as excinfo:
        await execute(_intent(args={"command": "false"}))

    assert excinfo.value.error.code == "sandbox_exec_failed"
    assert sandbox.killed is True


@pytest.mark.skipif(
    os.getenv("CHORUS_RUN_SANDBOX_SMOKE") != "1",
    reason="set CHORUS_RUN_SANDBOX_SMOKE=1 with a local opensandbox-server to smoke",
)
async def test_smoke_real_opensandbox_server():
    domain = os.environ["CHORUS_SANDBOX_DOMAIN"]
    api_key = os.getenv("CHORUS_SANDBOX_API_KEY", "")
    backend = OpenSandboxBackend(domain=domain, api_key=api_key)
    execute = make_sandbox_executor(backend)

    result = await execute(_intent(args={"command": "echo chorus"}))

    assert result.ok is True
    assert "chorus" in result.content
