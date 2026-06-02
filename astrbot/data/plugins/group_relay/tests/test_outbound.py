"""S4.1 判据（离线纯逻辑）：出站按 bot_id 选实例、用 group_key 重建会话、send_by_session。

只测 outbound.py（无 astrbot 依赖）：假 context/platform 桩 + 假 session/chain 工厂。
真实"curl /outbound → bot 在群发出消息"在 AstrBot 进程里手动验（需配 telegram bot）。
运行： orchestrator/.venv/Scripts/python -m pytest astrbot/data/plugins/group_relay/tests
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 插件目录，导入 outbound

from outbound import do_outbound, parse_target  # noqa: E402


class _FakePlatform:
    def __init__(self):
        self.sent = []

    async def send_by_session(self, session, chain):
        self.sent.append((session, chain))


class _FakeContext:
    def __init__(self, insts: dict):
        self._insts = insts

    def get_platform_inst(self, platform_id):
        return self._insts.get(platform_id)


def _make_session(bot_id, mt, sid):
    return ("session", bot_id, mt, sid)


def _make_chain(text):
    return ("chain", text)


# ---- parse_target ----


def test_parse_target_swaps_platform_for_bot():
    assert parse_target("telegram_main:GroupMessage:123", "bot_chen") == (
        "bot_chen",
        "GroupMessage",
        "123",
    )


def test_parse_target_rejects_bad_group_key():
    for bad in ["", "onlyone", "a:b", "a::c"]:
        with pytest.raises(ValueError):
            parse_target(bad, "bot")


def test_parse_target_requires_bot_id():
    with pytest.raises(ValueError):
        parse_target("p:GroupMessage:s", "")


# ---- do_outbound ----


def test_outbound_sends_via_selected_bot():
    plat = _FakePlatform()
    ctx = _FakeContext({"bot_chen": plat})
    cmd = {"group_key": "telegram_main:GroupMessage:42", "bot_id": "bot_chen", "text": "你好"}

    body, status = asyncio.run(
        do_outbound(ctx, cmd, make_session=_make_session, make_chain=_make_chain)
    )

    assert status == 200 and body["ok"] is True
    assert body["session"] == "bot_chen:GroupMessage:42"
    assert plat.sent == [(("session", "bot_chen", "GroupMessage", "42"), ("chain", "你好"))]


def test_outbound_unknown_bot_returns_404():
    ctx = _FakeContext({"bot_chen": _FakePlatform()})
    cmd = {"group_key": "p:GroupMessage:1", "bot_id": "ghost", "text": "x"}
    body, status = asyncio.run(
        do_outbound(ctx, cmd, make_session=_make_session, make_chain=_make_chain)
    )
    assert status == 404 and body["ok"] is False


def test_outbound_bad_payload_returns_400():
    ctx = _FakeContext({})
    for cmd in [{}, {"group_key": "p:t:s"}, {"bot_id": "b", "text": "x"}, "notdict"]:
        body, status = asyncio.run(
            do_outbound(ctx, cmd, make_session=_make_session, make_chain=_make_chain)
        )
        assert status == 400 and body["ok"] is False
