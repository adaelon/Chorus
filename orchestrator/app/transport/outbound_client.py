"""S4.3: 大脑出站客户端——把"某 contact 发言"路由到对应 bot 实例。

链路：大脑决定 contact X 说话 → 查 X.bot_ref（AstrBot platform 实例 id）→ POST
group_relay 桥 `/outbound {group_key, bot_id, text}` → 桥按 bot_id 选实例 send_by_session。
contact 未绑定 bot_ref 则不发（返回 ok=False）。

`send` 可注入以离线测试（默认 httpx POST）；自动把发言推进 telegram 群的 wiring（在
turn/synthesize 产出时调本客户端）留 S4.4 端到端。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

# contact_id -> bot_ref（AstrBot 实例 id）| None
BotRefProvider = Callable[[str], Awaitable[str | None]]
# (url, json) -> (status_code, body)
Sender = Callable[[str, dict], Awaitable[tuple[int, dict]]]

DEFAULT_BRIDGE_URL = "http://127.0.0.1:9876"


async def _httpx_send(url: str, payload: dict) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {}
        return r.status_code, body


class OutboundClient:
    def __init__(
        self,
        bridge_url: str,
        bot_ref_provider: BotRefProvider,
        *,
        send: Sender | None = None,
    ) -> None:
        self._url = bridge_url.rstrip("/") + "/outbound"
        self._resolve = bot_ref_provider
        self._send = send or _httpx_send

    async def speak(self, group_key: str, contact_id: str, text: str) -> dict:
        """以 contact 绑定的 bot 身份在群里发言。未绑定 bot_ref → ok=False，不发。"""
        bot_ref = await self._resolve(contact_id)
        if not bot_ref:
            return {"ok": False, "error": f"contact {contact_id!r} 未绑定 bot_ref"}
        status, body = await self._send(
            self._url, {"group_key": group_key, "bot_id": bot_ref, "text": text}
        )
        return {"ok": status == 200, "status": status, "bot_id": bot_ref, "bridge": body}
