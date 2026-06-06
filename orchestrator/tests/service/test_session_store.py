"""S12c: SessionStore reuse per group_key + readiness wired into the gate."""

from __future__ import annotations

import pytest

from app.execution_runtime import ToolExecutorGate
from app.execution_sandbox import (
    FakeBackend,
    SessionStore,
    make_pooled_sandbox_executor,
    make_real_executor,
)
from app.nodes.tool_dispatch import ToolDispatchError, tool_dispatch
from app.run_ctx import current_group_key
from app.state import GroupState, ToolCallIntent


def _intent(**kw) -> ToolCallIntent:
    data = {"call_id": "c1", "kind": "sandbox_exec", "tool_name": "python"}
    data.update(kw)
    return ToolCallIntent(**data)


# --- SessionStore -----------------------------------------------------------


async def test_store_reuses_one_session_per_group_key():
    backend = FakeBackend()
    store = SessionStore(backend)

    s1 = await store.acquire("g")
    s2 = await store.acquire("g")

    assert s1 is s2
    assert len(backend.sessions) == 1  # opened once


async def test_store_isolates_sessions_by_group_key():
    backend = FakeBackend()
    store = SessionStore(backend)

    a = await store.acquire("g-a")
    b = await store.acquire("g-b")

    assert a is not b
    assert len(backend.sessions) == 2


async def test_store_release_closes_and_drops_session():
    backend = FakeBackend()
    store = SessionStore(backend)
    session = await store.acquire("g")

    await store.release("g")

    assert session.closed is True
    # a fresh acquire opens a new session after release
    again = await store.acquire("g")
    assert again is not session
    assert len(backend.sessions) == 2


async def test_release_all_closes_every_session():
    backend = FakeBackend()
    store = SessionStore(backend)
    await store.acquire("g-a")
    await store.acquire("g-b")

    await store.release_all()

    assert all(s.closed for s in backend.sessions)


# --- pooled executor (reuse, no per-call close) ----------------------------


async def test_pooled_executor_reuses_session_within_a_run():
    backend = FakeBackend(stdout="x")
    store = SessionStore(backend)
    execute = make_pooled_sandbox_executor(store)

    token = current_group_key.set("g")
    try:
        await execute(_intent(args={"command": "a"}))
        await execute(_intent(args={"command": "b"}))
    finally:
        current_group_key.reset(token)

    assert len(backend.sessions) == 1  # same session reused
    session = backend.sessions[0]
    assert session.events == ["run", "run"]  # not closed between calls
    assert session.closed is False


async def test_pooled_executor_skill_writes_then_runs_on_reused_session():
    backend = FakeBackend()
    store = SessionStore(backend)
    execute = make_pooled_sandbox_executor(store)

    token = current_group_key.set("g")
    try:
        await execute(
            _intent(kind="sandbox_skill", args={"files": {"/s/SKILL.md": "# s"}, "command": "go"})
        )
    finally:
        current_group_key.reset(token)

    assert backend.sessions[0].events == ["write_files", "run"]  # no close


async def test_make_real_executor_prefers_store_over_per_call_backend():
    backend = FakeBackend(stdout="pooled")
    store = SessionStore(backend)
    execute = make_real_executor(sandbox_store=store)

    token = current_group_key.set("g")
    try:
        r1 = await execute(_intent(args={"command": "a"}))
        await execute(_intent(args={"command": "b"}))
    finally:
        current_group_key.reset(token)

    assert r1.content == "pooled"
    assert len(backend.sessions) == 1  # reused, not opened per call


# --- tool_dispatch injects group_key so the store keys correctly -----------


async def test_tool_dispatch_drives_pooled_reuse_across_calls():
    backend = FakeBackend()
    store = SessionStore(backend)
    execute = make_pooled_sandbox_executor(store)

    # two dispatch calls in the same run (same group_key) reuse one session;
    # tool_dispatch sets current_group_key from state.group_key.
    await tool_dispatch(
        GroupState(group_key="run-1", pending_tools=[_intent(args={"command": "a"})]),
        execute=execute,
    )
    await tool_dispatch(
        GroupState(group_key="run-1", pending_tools=[_intent(args={"command": "b"})]),
        execute=execute,
    )

    assert len(backend.sessions) == 1


# --- readiness wired into the gate -> S11d degraded edge --------------------


async def test_backend_readiness_down_raises_sandbox_unavailable_through_gate():
    backend = FakeBackend(ready=False)
    store = SessionStore(backend)
    gate = ToolExecutorGate(
        make_pooled_sandbox_executor(store), readiness_probe=backend.readiness
    )

    with pytest.raises(ToolDispatchError) as excinfo:
        await gate(_intent(requires_sandbox=True))

    assert excinfo.value.error.code == "sandbox_unavailable"
    assert excinfo.value.sandbox_ready is False
    assert len(backend.sessions) == 0  # never opened a session


async def test_readiness_down_routes_to_degraded_via_tool_dispatch():
    backend = FakeBackend(ready=False)
    store = SessionStore(backend)
    gate = ToolExecutorGate(
        make_pooled_sandbox_executor(store), readiness_probe=backend.readiness
    )

    out = await tool_dispatch(
        GroupState(group_key="g", pending_tools=[_intent(requires_sandbox=True)]),
        execute=gate,
    )

    assert out["run_status"] == "degraded"
    assert out["sandbox_ready"] is False
    assert out["last_tool_error"].code == "sandbox_unavailable"
