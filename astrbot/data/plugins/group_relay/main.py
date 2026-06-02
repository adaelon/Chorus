"""group_relay：Chorus ↔ AstrBot 的窄消息桥（S4.1 出站 + S4.2 入站）。

职责红线（技术方案 §1）：本插件只搬字节，不含任何业务智能（LLM/人设/调度/记忆全在
编排服务）。
- **出站**（S4.1）：编排服务决定"以 bot X 身份在某群发言"→ POST 本插件自起的 aiohttp 桥
  → 按 bot_id 选 platform 实例 `send_by_session` 发出。
- **入站**（S4.2）：群消息钩子 → 规范化 InboundMsg → POST 大脑 `/inbound`；按
  (group_key, native_msg_id) 去重（N bot 同条只转一次）；`stop_event()` 防 AstrBot 自动回复。

为何自起 aiohttp 而非复用 dashboard：dashboard 的 /api/plug 路由受 JWT 鉴权，服务间
调用不便；自起一个 127.0.0.1 专用桥端口、无鉴权、与 dashboard 解耦，契合"窄桥"。

多 bot 间 AI 发言识别 / bot_ref 映射 = S4.3；大脑侧 InboundMsg 处理 + 端到端 = S4.4。
"""

from __future__ import annotations

import logging
from sys import maxsize

import aiohttp
from aiohttp import web
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

from .inbound import Dedup, decide, make_inbound_msg
from .outbound import do_outbound

logger = logging.getLogger("astrbot")

DEFAULT_BRIDGE_PORT = 9876
DEFAULT_BRAIN_URL = "http://127.0.0.1:8900"


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
        cfg = config or {}
        self._port = int(cfg.get("bridge_port", DEFAULT_BRIDGE_PORT))
        self._brain_url = str(cfg.get("brain_url", DEFAULT_BRAIN_URL)).rstrip("/")
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None
        self._dedup = Dedup()

    async def initialize(self) -> None:
        self._session = aiohttp.ClientSession()
        app = web.Application()
        app.router.add_post("/outbound", self._handle_outbound)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", self._port)
        await site.start()
        self._runner = runner
        logger.info(
            f"group_relay 桥已起：出站 http://127.0.0.1:{self._port}/outbound；"
            f"入站转发 → {self._brain_url}/inbound"
        )

    async def terminate(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ---- 出站（S4.1）----

    async def _handle_outbound(self, request: web.Request) -> web.Response:
        try:
            cmd = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "请求体非合法 JSON"}, status=400)
        body, status = await do_outbound(
            self.context, cmd, make_session=_make_session, make_chain=_make_chain
        )
        return web.json_response(body, status=status)

    # ---- 入站（S4.2）----

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        """群消息 → 去重 → 规范化转发大脑 → stop_event 防 AstrBot 自动回复。"""
        action = decide(
            group_key=event.unified_msg_origin,
            msg_id=event.message_obj.message_id,
            sender_id=event.get_sender_id(),
            self_id=event.get_self_id(),
            text=event.get_message_str(),
            dedup=self._dedup,
        )
        if action == "ignore":
            return
        if action == "forward":
            msg = make_inbound_msg(
                group_key=event.unified_msg_origin,
                platform=event.get_platform_name(),
                sender_id=event.get_sender_id(),
                sender_name=event.get_sender_name(),
                sender_kind="human",  # 多 bot 间 AI 识别留 S4.3
                text=event.get_message_str(),
                native_msg_id=event.message_obj.message_id,
                ts=event.message_obj.timestamp,
            )
            try:
                await self._post_inbound(msg)
            except Exception as e:  # noqa: BLE001
                logger.error(f"group_relay 入站转发失败：{e}")
        # forward 与 stop_only 都截断：阻止 AstrBot 默认 LLM 自动回复（技术方案 §2.1 坑②）。
        event.stop_event()

    async def _post_inbound(self, msg: dict) -> None:
        assert self._session is not None
        async with self._session.post(f"{self._brain_url}/inbound", json=msg) as resp:
            await resp.read()
