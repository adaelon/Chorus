"""S12a: sandbox backend protocol + neutral translation + kind dispatch.

S11 locked the tool-dispatch contract (`ToolExecutor`/`ToolResult`/
`ToolDispatchError`). S12 plugs real backends in behind a neutral
`SandboxBackend` protocol so swapping backend = swapping a factory
(§6.23). This slice (S12a) ships only the protocol, the shared intent→
session translation, the kind dispatcher, and an offline `FakeBackend`.

Deferred on purpose: real OpenSandbox SDK (S12b), session reuse (S12c —
S12a opens/closes a fresh session per call), MCP wiring (S12d — `mcp_call`
is a placeholder), and service wiring (S12e).

Intent `args` conventions used by the translation layer:
- `sandbox_exec`  : ``args["command"]`` — the command/code run in the sandbox.
- `sandbox_skill` : ``args["files"]`` (``{path: content}``) written first,
  then ``args["command"]`` run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .nodes.tool_dispatch import ToolDispatchError, ToolExecutor
from .state import ToolCallIntent, ToolResult


@dataclass
class ExecResult:
    """Outcome of one command run inside a sandbox session."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class SandboxSession(Protocol):
    """One live sandbox session; backends translate their SDK to this shape."""

    session_id: str

    async def run(self, command: str, *, timeout_ms: int | None = None) -> ExecResult: ...

    async def write_files(self, files: Mapping[str, str]) -> None: ...

    async def read_file(self, path: str) -> str: ...

    async def close(self) -> None: ...


class SandboxBackend(Protocol):
    """A sandbox provider; swapping backend = swapping this factory (§6.23).

    ``group_key`` is accepted (for S12c session reuse) but optional, so the
    S12a translation layer can open anonymous fresh sessions.
    """

    async def open(
        self, *, group_key: str | None = None, profile: str | None = None
    ) -> SandboxSession: ...

    async def reconnect(self, session_id: str) -> SandboxSession: ...

    async def readiness(self) -> bool: ...


SessionProvider = Callable[[ToolCallIntent], Awaitable[SandboxSession]]


def make_sandbox_executor(backend: SandboxBackend) -> ToolExecutor:
    """Translate sandbox intents into session calls, shared by all backends.

    S12a owns the session lifecycle here (open→translate→close per call); S12c
    swaps in a `SessionStore` for reuse. A non-zero exit raises
    `ToolDispatchError` so S11c/S11d route it as a tool failure.
    """

    async def execute(intent: ToolCallIntent) -> ToolResult:
        session = await backend.open(profile=intent.sandbox_profile)
        try:
            if intent.kind == "sandbox_skill":
                files = intent.args.get("files") or {}
                if files:
                    await session.write_files(files)
            command = intent.args.get("command", "")
            result = await session.run(command, timeout_ms=intent.timeout_ms)
            if result.exit_code != 0:
                raise ToolDispatchError(
                    "sandbox_exec_failed",
                    f"command exited with {result.exit_code}: {result.stderr}".rstrip(": "),
                    retryable=False,
                )
            return ToolResult(
                call_id=intent.call_id,
                tool_name=intent.tool_name,
                ok=True,
                content=result.stdout,
                data={"exit_code": result.exit_code, "stderr": result.stderr},
            )
        finally:
            await session.close()

    return execute


def make_real_executor(
    *,
    sandbox_backend: SandboxBackend | None = None,
    mcp_executor: ToolExecutor | None = None,
) -> ToolExecutor:
    """Dispatch a tool intent to the right adapter by ``intent.kind``.

    `sandbox_exec`/`sandbox_skill` go to the shared sandbox translator;
    `mcp_call` delegates to an injected MCP executor or — until S12d wires
    one — raises `NotImplementedError`.
    """
    sandbox_exec = make_sandbox_executor(sandbox_backend) if sandbox_backend else None

    async def execute(intent: ToolCallIntent) -> ToolResult:
        if intent.kind in ("sandbox_exec", "sandbox_skill"):
            if sandbox_exec is None:
                raise ToolDispatchError(
                    "sandbox_unavailable",
                    "no sandbox backend configured",
                    retryable=False,
                    sandbox_ready=False,
                )
            return await sandbox_exec(intent)
        if intent.kind == "mcp_call":
            if mcp_executor is None:
                raise NotImplementedError("mcp_call executor not wired yet (S12d)")
            return await mcp_executor(intent)
        raise ToolDispatchError(
            "unknown_kind", f"unknown tool intent kind: {intent.kind!r}", retryable=False
        )

    return execute


# --- offline fake backend (no external deps) -------------------------------


@dataclass
class FakeSession:
    """In-memory sandbox session; records call order for ordering assertions."""

    session_id: str
    exit_code: int = 0
    stdout: str = "ok"
    stderr: str = ""
    events: list[str] = field(default_factory=list)
    fs: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    async def run(self, command: str, *, timeout_ms: int | None = None) -> ExecResult:
        self.events.append("run")
        return ExecResult(stdout=self.stdout, stderr=self.stderr, exit_code=self.exit_code)

    async def write_files(self, files: Mapping[str, str]) -> None:
        self.events.append("write_files")
        self.fs.update(files)

    async def read_file(self, path: str) -> str:
        return self.fs[path]

    async def close(self) -> None:
        self.events.append("close")
        self.closed = True


class FakeBackend:
    """Offline `SandboxBackend` for tests; configurable exit code / output."""

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "ok",
        stderr: str = "",
        ready: bool = True,
    ) -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._ready = ready
        self.sessions: list[FakeSession] = []

    def _new_session(self) -> FakeSession:
        session = FakeSession(
            session_id=f"fake-{len(self.sessions)}",
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
        )
        self.sessions.append(session)
        return session

    async def open(
        self, *, group_key: str | None = None, profile: str | None = None
    ) -> FakeSession:
        return self._new_session()

    async def reconnect(self, session_id: str) -> FakeSession:
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        raise KeyError(session_id)

    async def readiness(self) -> bool:
        return self._ready
