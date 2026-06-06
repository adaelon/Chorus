"""S12a/S12c: sandbox backend protocol + neutral translation + kind dispatch.

S11 locked the tool-dispatch contract (`ToolExecutor`/`ToolResult`/
`ToolDispatchError`). S12 plugs real backends in behind a neutral
`SandboxBackend` protocol so swapping backend = swapping a factory (§6.23).

- S12a: protocol, shared intent→session translation, kind dispatcher, offline
  `FakeBackend`. One fresh session per call (open→translate→close).
- S12c: `SessionStore` reuses one session per `group_key` across a run
  (`make_pooled_sandbox_executor` keeps it open; `release` closes on run end/
  abort). The current `group_key` is read from `run_ctx.current_group_key`,
  injected by `tool_dispatch` (same pattern as turn/fanout, S7.3b).

Deferred: MCP (S12d), service wiring (S12e), pause/resume/renew TTL handling.

Intent `args` conventions used by the translation layer:
- `sandbox_exec`  : ``args["command"]`` — the command/code run in the sandbox.
- `sandbox_skill` : ``args["files"]`` (``{path: content}``) written first,
  then ``args["command"]`` run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .nodes.tool_dispatch import ToolDispatchError, ToolExecutor
from .run_ctx import current_group_key
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


async def _run_intent(session: SandboxSession, intent: ToolCallIntent) -> ToolResult:
    """Translate one sandbox intent into session calls (shared by all backends).

    `sandbox_skill` writes its files before running; a non-zero exit raises
    `ToolDispatchError` so S11c/S11d route it as a tool failure. Session
    lifecycle (open/close vs. reuse) is owned by the caller.
    """
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


def make_sandbox_executor(backend: SandboxBackend) -> ToolExecutor:
    """Open a fresh session per call, translate, then close (S12a, no reuse)."""

    async def execute(intent: ToolCallIntent) -> ToolResult:
        session = await backend.open(profile=intent.sandbox_profile)
        try:
            return await _run_intent(session, intent)
        finally:
            await session.close()

    return execute


class SessionStore:
    """Reuse one sandbox session per ``group_key`` across a run (S12c).

    The store owns the session lifecycle: `acquire` opens once then reuses;
    `release`/`release_all` close on run end or abort. Sessions stay open
    between calls so multi-step tasks keep their filesystem state (§6.23).
    """

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        self._sessions: dict[str, SandboxSession] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, group_key: str) -> SandboxSession:
        async with self._lock:
            session = self._sessions.get(group_key)
            if session is None:
                session = await self._backend.open(group_key=group_key)
                self._sessions[group_key] = session
            return session

    async def release(self, group_key: str) -> None:
        async with self._lock:
            session = self._sessions.pop(group_key, None)
        if session is not None:
            await session.close()

    async def release_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()


def make_pooled_sandbox_executor(store: SessionStore) -> ToolExecutor:
    """Translate over a session reused per run's ``group_key`` (S12c).

    The key comes from `run_ctx.current_group_key` (injected by `tool_dispatch`);
    the session is NOT closed per call — `SessionStore.release` owns that.
    """

    async def execute(intent: ToolCallIntent) -> ToolResult:
        group_key = current_group_key.get() or ""
        session = await store.acquire(group_key)
        return await _run_intent(session, intent)

    return execute


def make_real_executor(
    *,
    sandbox_backend: SandboxBackend | None = None,
    sandbox_store: SessionStore | None = None,
    mcp_executor: ToolExecutor | None = None,
) -> ToolExecutor:
    """Dispatch a tool intent to the right adapter by ``intent.kind``.

    `sandbox_exec`/`sandbox_skill` go to the pooled executor when a
    `sandbox_store` is given (S12c reuse), else a per-call sandbox executor
    over `sandbox_backend`; `mcp_call` delegates to an injected MCP executor
    or — until S12d wires one — raises `NotImplementedError`.
    """
    if sandbox_store is not None:
        sandbox_exec: ToolExecutor | None = make_pooled_sandbox_executor(sandbox_store)
    elif sandbox_backend is not None:
        sandbox_exec = make_sandbox_executor(sandbox_backend)
    else:
        sandbox_exec = None

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
