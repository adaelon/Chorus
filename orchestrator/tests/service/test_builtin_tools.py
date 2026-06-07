"""S14a: built-in in-process tools (fetch_url / web_search) + registry inclusion."""

from __future__ import annotations

from app.builtin_tools import BuiltinToolSession, builtin_tools
from app.execution_mcp import McpRegistry
from app.nodes.plan_stream import _plan_system
from app.state import ToolCallIntent


async def _fake_fetch(args):
    return f"FETCHED {args['url']}"


async def _fake_search(args):
    return f"RESULTS for {args['query']}"


def _session():
    return BuiltinToolSession(builtin_tools(fetch_url=_fake_fetch, web_search=_fake_search))


# --- BuiltinToolSession (mcp-compatible shapes) -----------------------------


async def test_builtin_session_lists_tools():
    result = await _session().list_tools()
    names = {t.name for t in result.tools}
    assert names == {"fetch_url", "web_search"}


async def test_builtin_session_calls_tool_and_wraps_result():
    out = await _session().call_tool("web_search", {"query": "chorus"})
    assert out.isError is False
    assert out.content[0].text == "RESULTS for chorus"


async def test_builtin_session_unknown_tool_is_error():
    out = await _session().call_tool("nope", {})
    assert out.isError is True


async def test_builtin_tool_exception_becomes_error_result():
    async def boom(_args):
        raise RuntimeError("down")

    session = BuiltinToolSession(builtin_tools(web_search=boom))
    out = await session.call_tool("web_search", {"query": "x"})
    assert out.isError is True
    assert "down" in out.content[0].text


# --- registry includes built-ins by default ---------------------------------


async def test_registry_includes_builtins_and_routes_to_them():
    # inject fake built-in impls so refresh/route stay offline (no real DDGS/httpx)
    reg = McpRegistry([], builtins=builtin_tools(fetch_url=_fake_fetch, web_search=_fake_search))
    await reg.refresh()
    names = {t["name"] for t in reg.catalog()}
    assert {"fetch_url", "web_search"} <= names

    out = await reg.make_executor()(
        ToolCallIntent(call_id="c1", kind="mcp_call", tool_name="web_search", args={"query": "x"})
    )
    assert out.ok is True
    assert out.content == "RESULTS for x"  # routed to the built-in session


# --- planner prompt: built-ins listed; sandbox toggle -----------------------


def test_plan_prompt_lists_builtins_and_respects_sandbox_flag():
    catalog = [{"name": "web_search", "description": "联网搜索"}]
    with_sandbox = _plan_system(catalog, has_sandbox=True)
    assert "sandbox_exec" in with_sandbox and "web_search" in with_sandbox

    no_sandbox = _plan_system(catalog, has_sandbox=False)
    assert "sandbox_exec" not in no_sandbox  # 无沙箱 → 不列
    assert "web_search" in no_sandbox  # 内置工具仍在
