"""group_relay：Chorus ↔ AstrBot 的窄消息桥（S4.1：出站）。

职责红线（技术方案 §1）：本插件只搬字节，不含任何业务智能（LLM/人设/调度/记忆全在
编排服务）。S4.1 只做**出站**——编排服务决定"以 bot X 身份在某群发言"后，POST 到本插件
自起的 aiohttp 桥，按 bot_id 选 AstrBot platform 实例 `send_by_session` 发出。

为何自起 aiohttp 而非复用 dashboard：dashboard 的 /api/plug 路由受 JWT 鉴权，服务间
调用不便；自起一个 127.0.0.1 专用桥端口、无鉴权、与 dashboard 解耦，契合"窄桥"。

入站/去重/stop_event/多 bot = S4.2+。
"""

from __future__ import annotations

import logging

from aiohttp import web
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

from .outbound import do_outbound

logger = logging.getLogger("astrbot")

DEFAULT_BRIDGE_PORT = 9876


def _make_session(bot_id: str, message_type: str, session_id: str) -> MessageSession:
    # 平台段用 bot_id（= 目标 bot 的 platform 实例 id）→ 群里显示为 bot X 在说话。
    return MessageSession(
        platform_name=bot_id,
        message_type=MessageType(message_type),
        session_id=session_id,
    )


def _make_chain(text: str) -> MessageChain:
    return MessageChain(chain=[Plain(text)])


class GroupRelay(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self._port = int((config or {}).get("bridge_port", DEFAULT_BRIDGE_PORT))
        self._runner: web.AppRunner | None = None

    async def initialize(self) -> None:
        app = web.Application()
        app.router.add_post("/outbound", self._handle_outbound)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", self._port)
        await site.start()
        self._runner = runner
        logger.info(f"group_relay 出站桥已起：http://127.0.0.1:{self._port}/outbound")

    async def terminate(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_outbound(self, request: web.Request) -> web.Response:
        try:
            cmd = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "请求体非合法 JSON"}, status=400)
        body, status = await do_outbound(
            self.context, cmd, make_session=_make_session, make_chain=_make_chain
        )
        return web.json_response(body, status=status)
