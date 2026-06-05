"""S11f P1 runtime helpers for the execution loop.

The execution graph keeps node semantics small; this module holds the
production-adjacent concerns that wrap it: durable sqlite checkpointer
selection, SSE heartbeat framing, and audit projection from trace state.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .nodes.tool_dispatch import ToolDispatchError, ToolExecutor
from .state import TraceEvent

ReadinessProbe = Callable[[], Awaitable[bool]]


@asynccontextmanager
async def execution_checkpointer(db_path: str | Path):
    """Open the durable P1 checkpointer used by the execution subgraph."""
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        yield saver


def execution_sse(event: dict[str, Any]) -> str:
    """Encode one execution-loop event as an SSE data frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_with_heartbeat(
    source: AsyncIterable[str],
    *,
    interval: float = 15.0,
    heartbeat: str = ": heartbeat\n\n",
) -> AsyncIterator[str]:
    """Yield source frames and emit SSE heartbeat comments while it is idle."""
    iterator = source.__aiter__()
    pending = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield heartbeat
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                break
            yield frame
            pending = asyncio.create_task(iterator.__anext__())
    finally:
        if not pending.done():
            pending.cancel()


def project_trace_events(
    trace_events: Iterable[TraceEvent | dict[str, Any]],
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Project state trace events into queryable audit-log rows."""
    rows: list[dict[str, Any]] = []
    for raw in trace_events:
        event = raw if isinstance(raw, TraceEvent) else TraceEvent(**raw)
        rows.append(
            {
                "thread_id": thread_id,
                "run_id": event.run_id,
                "node": event.node,
                "status": event.status,
                "error": event.error,
                "message": event.message,
                "ts": event.ts,
                "data": dict(event.data),
            }
        )
    return rows


def filter_audit_rows(
    rows: Iterable[dict[str, Any]],
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
    node: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Filter projected audit rows by the fields operators need first."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if thread_id is not None and row.get("thread_id") != thread_id:
            continue
        if run_id is not None and row.get("run_id") != run_id:
            continue
        if node is not None and row.get("node") != node:
            continue
        if status is not None and row.get("status") != status:
            continue
        out.append(row)
    return out


async def http_readiness_probe(url: str, *, timeout: float = 2.0) -> bool:
    """Probe a Shipyard-compatible readiness endpoint with stdlib HTTP."""

    def _open() -> bool:
        try:
            with urlopen(url, timeout=timeout) as resp:  # noqa: S310 - operator-configured URL
                return 200 <= resp.status < 400
        except (OSError, URLError):
            return False

    return await asyncio.to_thread(_open)


class ToolExecutorGate:
    """Wrap a real tool executor with Shipyard readiness and concurrency control."""

    def __init__(
        self,
        execute: ToolExecutor,
        *,
        readiness_probe: ReadinessProbe | None = None,
        max_concurrency: int = 1,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._execute = execute
        self._readiness_probe = readiness_probe
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def __call__(self, intent):
        if intent.requires_sandbox and self._readiness_probe is not None:
            ready = await self._readiness_probe()
            if not ready:
                raise ToolDispatchError(
                    "sandbox_unavailable",
                    "shipyard readiness probe failed",
                    retryable=False,
                    sandbox_ready=False,
                )
        async with self._semaphore:
            return await self._execute(intent)
