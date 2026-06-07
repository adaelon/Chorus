"""S13f.a: MCP server registry CRUD + McpRegistry (catalog + routing executor)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.execution_mcp import McpRegistry
from app.nodes.tool_dispatch import ToolDispatchError
from app.service import create_app
from app.state import ToolCallIntent, ToolResult


def _app(tmp_path):
    return create_app(checkpointer=MemorySaver(), registry_db_path=str(tmp_path / "reg.sqlite"))


# --- CRUD -------------------------------------------------------------------


def test_mcp_server_crud(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/mcp-servers").json() == []
        body = {"id": "fs", "name": "filesystem", "transport": "stdio", "command": "npx", "args": ["-y", "srv"]}
        assert client.post("/mcp-servers", json=body).status_code == 200
        assert client.post("/mcp-servers", json=body).status_code == 409  # dup
        rows = client.get("/mcp-servers").json()
        assert len(rows) == 1 and rows[0]["command"] == "npx" and rows[0]["args"] == ["-y", "srv"]
        assert client.put("/mcp-servers/fs", json={**body, "name": "FS"}).json()["name"] == "FS"
        assert client.put("/mcp-servers/nope", json=body).status_code == 404
        assert client.delete("/mcp-servers/fs").json() == {"deleted": "fs"}
        assert client.delete("/mcp-servers/fs").status_code == 404


# --- McpRegistry over fake providers ----------------------------------------


class _Spec:
    def __init__(self, name, tools):
        self.name = name
        self._tools = tools


class _Tool:
    def __init__(self, name, description=""):
        self.name = name
        self.description = description


class _ListResult:
    def __init__(self, tools):
        self.tools = tools


class _FakeSession:
    def __init__(self, spec):
        self._spec = spec
        self.calls = []

    async def list_tools(self):
        return _ListResult([_Tool(n) for n in self._spec._tools])

    async def call_tool(self, name, arguments=None):
        self.calls.append((self._spec.name, name, arguments))

        class _R:
            content = [type("T", (), {"type": "text", "text": f"{name}@{self._spec.name}"})()]
            isError = False
            structuredContent = None

        return _R()


def _factory(spec):
    @asynccontextmanager
    async def provider():
        yield _FakeSession(spec)

    return provider


async def test_registry_aggregates_catalog_and_routes_by_tool_name():
    a = _Spec("srvA", ["read_file", "write_file"])
    b = _Spec("srvB", ["search"])
    reg = McpRegistry([a, b], provider_factory=_factory, include_builtins=False)
    await reg.refresh()

    names = {t["name"] for t in reg.catalog()}
    assert names == {"read_file", "write_file", "search"}

    execute = reg.make_executor()
    out = await execute(ToolCallIntent(call_id="c1", kind="mcp_call", tool_name="search", args={"q": "x"}))
    assert isinstance(out, ToolResult)
    assert out.content == "search@srvB"  # routed to the server that has the tool


async def test_registry_unknown_tool_raises():
    reg = McpRegistry([_Spec("srvA", ["read_file"])], provider_factory=_factory, include_builtins=False)
    await reg.refresh()
    with pytest.raises(ToolDispatchError) as excinfo:
        await reg.make_executor()(
            ToolCallIntent(call_id="c1", kind="mcp_call", tool_name="nope")
        )
    assert excinfo.value.error.code == "mcp_unknown_tool"


async def test_registry_skips_unreachable_server():
    def bad_factory(spec):
        @asynccontextmanager
        async def provider():
            raise RuntimeError("connection refused")
            yield  # pragma: no cover

        return provider

    reg = McpRegistry([_Spec("down", ["x"])], provider_factory=bad_factory, include_builtins=False)
    await reg.refresh()  # must not raise
    assert reg.catalog() == []
