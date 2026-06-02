"""S4.3 判据：大脑出站客户端把"某 contact 发言"解析 bot_ref 并 POST 桥 /outbound。

注入假 send（不联网）+ 假 bot_ref_provider，断言：解析 contact→bot_ref、payload 正确、
未绑定 bot_ref 不发。桥侧"按 bot_id 精确路由到对应 bot"由插件 smoke_outbound 验。
"""

from __future__ import annotations

from app.outbound_client import OutboundClient


def _provider(mapping):
    async def provider(contact_id):
        return mapping.get(contact_id)

    return provider


async def test_speak_resolves_bot_ref_and_posts():
    sent = []

    async def fake_send(url, payload):
        sent.append((url, payload))
        return 200, {"ok": True, "session": "telegram_chen:GroupMessage:42"}

    client = OutboundClient(
        "http://127.0.0.1:9876",
        _provider({"chen": "telegram_chen"}),
        send=fake_send,
    )
    res = await client.speak("telegram_main:GroupMessage:42", "chen", "大家好")

    assert res["ok"] is True and res["bot_id"] == "telegram_chen"
    url, payload = sent[0]
    assert url == "http://127.0.0.1:9876/outbound"
    # 关键：以 contact 绑定的 bot_ref 作为 bot_id 路由
    assert payload == {
        "group_key": "telegram_main:GroupMessage:42",
        "bot_id": "telegram_chen",
        "text": "大家好",
    }


async def test_speak_without_bot_ref_does_not_send():
    sent = []

    async def fake_send(url, payload):
        sent.append((url, payload))
        return 200, {}

    client = OutboundClient("http://x", _provider({}), send=fake_send)  # 无绑定
    res = await client.speak("g", "ghost", "hi")

    assert res["ok"] is False and "bot_ref" in res["error"]
    assert sent == []  # 未绑定不发


async def test_speak_propagates_bridge_failure():
    async def fake_send(url, payload):
        return 404, {"ok": False, "error": "bot not found"}

    client = OutboundClient("http://x", _provider({"a": "bot_a"}), send=fake_send)
    res = await client.speak("g", "a", "hi")
    assert res["ok"] is False and res["status"] == 404
