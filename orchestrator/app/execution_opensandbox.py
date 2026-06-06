"""S12b: OpenSandboxBackend over the `opensandbox` pip SDK, v0.1.9 (§6.23).

Implements the S12a `SandboxBackend`/`SandboxSession` protocol against a
self-hosted OpenSandbox server. The SDK is **async-native** (verified against
0.1.9 by introspection), so calls are awaited directly — no thread offload.

All `opensandbox` imports are lazy (inside the factory / methods), so this
module imports without the package installed; it's an optional extra. Adapter
tests inject async fakes shaped like the SDK; the real server is smoke-verified
(`CHORUS_RUN_SANDBOX_SMOKE=1`).

Verified SDK shapes (0.1.9):
- ``await Sandbox.create(connection_config=ConnectionConfig(domain=, api_key=))``
- ``await sandbox.commands.run(cmd) -> Execution`` with ``.exit_code: int|None``
  and ``.logs.stdout / .logs.stderr : list[OutputMessage]`` (each ``.text: str``)
- ``await sandbox.files.write_files([WriteEntry(path=, data=)])``
- ``await sandbox.files.read_file(path) -> str``
- ``await sandbox.kill()`` ; ``sandbox.id`` is the session id
- session reuse (``connect``/``pause``/``resume``/``renew``) is S12c.

Deferred: session reuse (S12c), MCP (S12d), service wiring (S12e),
sandbox-level timeout passthrough.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from .execution_sandbox import ExecResult

# An async factory returns a live opensandbox Sandbox object.
SandboxFactory = Callable[[], Awaitable[Any]]
ReadinessProbe = Callable[[], Awaitable[bool]]


def _default_factory(domain: str, api_key: str) -> SandboxFactory:
    """Build the real-SDK async factory; import is lazy so the module loads bare."""

    async def create() -> Any:
        from opensandbox import Sandbox
        from opensandbox.config.connection import ConnectionConfig

        return await Sandbox.create(
            connection_config=ConnectionConfig(domain=domain, api_key=api_key)
        )

    return create


def _default_readiness(domain: str) -> ReadinessProbe:
    async def probe() -> bool:
        from .execution_runtime import http_readiness_probe

        return await http_readiness_probe(domain)

    return probe


def _join_output(messages: Any) -> str:
    """Join `list[OutputMessage]` (each `.text`) into a string; tolerate a str."""
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, Iterable):
        return "".join(getattr(m, "text", "") or "" for m in messages)
    return ""


class OpenSandboxSession:
    """Wrap one opensandbox Sandbox as an async `SandboxSession`."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox
        self.session_id = str(getattr(sandbox, "id", "") or "")

    async def run(self, command: str, *, timeout_ms: int | None = None) -> ExecResult:
        # S12b ignores timeout_ms (sandbox-level timeout passthrough deferred);
        # the loop's retry_budget/gate already bound runaway calls.
        execution = await self._sandbox.commands.run(command)
        logs = getattr(execution, "logs", None)
        return ExecResult(
            stdout=_join_output(getattr(logs, "stdout", None)),
            stderr=_join_output(getattr(logs, "stderr", None)),
            exit_code=getattr(execution, "exit_code", 0) or 0,
        )

    async def write_files(self, files: Mapping[str, str]) -> None:
        from opensandbox.models.filesystem import WriteEntry

        entries = [WriteEntry(path=path, data=data) for path, data in files.items()]
        await self._sandbox.files.write_files(entries)

    async def read_file(self, path: str) -> str:
        return await self._sandbox.files.read_file(path)

    async def close(self) -> None:
        await self._sandbox.kill()


class OpenSandboxBackend:
    """`SandboxBackend` over a self-hosted OpenSandbox server.

    `factory`/`readiness_probe` are injectable so adapter tests run without the
    network; defaults bind to the real async SDK and an HTTP readiness probe.
    """

    def __init__(
        self,
        *,
        domain: str = "",
        api_key: str = "",
        factory: SandboxFactory | None = None,
        readiness_probe: ReadinessProbe | None = None,
    ) -> None:
        self._factory = factory or _default_factory(domain, api_key)
        self._readiness_probe = readiness_probe or _default_readiness(domain)

    async def open(
        self, *, group_key: str | None = None, profile: str | None = None
    ) -> OpenSandboxSession:
        sandbox = await self._factory()
        return OpenSandboxSession(sandbox)

    async def reconnect(self, session_id: str) -> OpenSandboxSession:
        # SDK supports Sandbox.connect(sandbox_id, ...); reuse lifecycle is S12c.
        raise NotImplementedError("session reuse is S12c")

    async def readiness(self) -> bool:
        return await self._readiness_probe()
