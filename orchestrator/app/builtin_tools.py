"""S14a: built-in in-process tools (§6.24) — fetch_url + web_search (DuckDuckGo).

A small default tool surface that works with zero external setup: exposed to the
planner as an in-process "MCP server" (mcp-compatible session shapes), so
`McpRegistry` includes them by default and `mcp_call` routing reaches them. No
external server, no API key. python/bash stay in the isolated sandbox (never on
the host) — these built-ins are network-only and harmless.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

ToolImpl = Callable[[dict], Awaitable[Any]]


# --- mcp-compatible result shapes (duck-typed; no mcp SDK import) -----------
class _Text:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Result:
    def __init__(self, text: str, *, is_error: bool = False) -> None:
        self.content = [_Text(text)]
        self.isError = is_error
        self.structuredContent = None


class _ToolDef:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


class _ListResult:
    def __init__(self, tools: list) -> None:
        self.tools = tools


# --- tool implementations ---------------------------------------------------
async def default_fetch_url(args: dict) -> str:
    """抓取一个网页 URL 的文本内容（httpx GET，截断超长）。"""
    import httpx

    url = (args.get("url") or "").strip()
    if not url:
        return "缺少 url 参数"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Chorus/1.0"})
        resp.raise_for_status()
        return resp.text[:8000]


async def default_web_search(args: dict) -> str:
    """DuckDuckGo 联网搜索（免 key），返回前几条 标题/链接/摘要。"""
    query = (args.get("query") or "").strip()
    if not query:
        return "缺少 query 参数"
    max_results = int(args.get("max_results") or 5)

    def _search() -> list[dict]:
        from ddgs import DDGS  # 惰性：未装 ddgs 时只在用到 web_search 才报错

        with DDGS() as ddgs:
            return ddgs.text(query, max_results=max_results)

    try:
        results = await asyncio.to_thread(_search)
    except Exception as e:  # noqa: BLE001 - ddgs 未装 / 限流 / 网络
        return f"搜索失败：{e}"
    lines = [
        f"- {r.get('title', '')}\n  {r.get('href') or r.get('url', '')}\n  {r.get('body', '')}"
        for r in (results or [])
    ]
    return "\n".join(lines) or "（无结果）"


def builtin_tools(
    *, fetch_url: ToolImpl = default_fetch_url, web_search: ToolImpl = default_web_search
) -> dict:
    """内置工具表（impl 可注入便于离线测）。"""
    return {
        "fetch_url": {"description": "抓取一个网页 URL 的文本内容（参数 url）", "impl": fetch_url},
        "web_search": {
            "description": "用 DuckDuckGo 联网搜索，返回前几条结果（参数 query）",
            "impl": web_search,
        },
    }


class BuiltinToolSession:
    """In-process session exposing built-in tools in mcp-compatible shapes."""

    def __init__(self, tools: dict | None = None) -> None:
        self._tools = tools if tools is not None else builtin_tools()

    async def list_tools(self) -> _ListResult:
        return _ListResult([_ToolDef(n, t["description"]) for n, t in self._tools.items()])

    async def call_tool(self, name: str, arguments: dict | None = None) -> _Result:
        tool = self._tools.get(name)
        if tool is None:
            return _Result(f"未知内置工具 {name}", is_error=True)
        try:
            return _Result(str(await tool["impl"](arguments or {})))
        except Exception as e:  # noqa: BLE001
            return _Result(f"工具出错：{e}", is_error=True)


def builtin_provider(tools: dict | None = None):
    """An McpSessionProvider yielding a `BuiltinToolSession`（registry 默认含它）。"""

    @asynccontextmanager
    async def provider():
        yield BuiltinToolSession(tools)

    return provider
