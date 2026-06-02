"""S4.4: telegram ↔ 圆桌 的驱动器（大脑侧）。

桥把群里的人类消息 POST 到 `/relay/inbound`；本驱动器据此起一场圆桌、**后台自动多轮**
推进，每轮 AI 发言经 `OutboundClient` 以对应 bot 身份推回群（N bot 轮流冒泡）。

为什么用后台 task + 步进 ainvoke：kimi 每轮慢（~分钟级），不能在一次 HTTP 请求里同步跑完
（桥的 POST 会超时）。故 `/relay/inbound` 立即返回，讨论在后台跑；每轮 `ainvoke` 推进一步
（圆桌 human_in_loop=True 每轮停在 human_gate），驱动器 `resume` 续轮并把新发言推群。

S4.4a：自动多轮 + 推送。S4.4b：讨论中人类消息入队 → 每轮 human_gate 处消费 →
`resume({interject:text})` → 该插话进 history + 预算闸归零 → 圆桌改向继续。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from langgraph.types import Command

from .runtime import TurnDone, iter_events
from .state import AgentSlot, Msg

logger = logging.getLogger("chorus.relay")

RosterProvider = Callable[[], Awaitable[list[str]]]


def canonical_thread(group_key: str) -> str:
    """群会话的稳定 thread_id：去掉平台（bot）段，使多 bot 同群归一到一个圆桌会话。"""
    return group_key.split(":", 1)[1] if ":" in group_key else group_key


class RelayDriver:
    def __init__(
        self,
        graph,
        outbound,
        roster_provider: RosterProvider,
        *,
        max_turns: int = 6,
    ) -> None:
        self._graph = graph
        self._outbound = outbound  # OutboundClient（.speak(group_key, contact_id, text)）
        self._roster_provider = roster_provider
        self._max_turns = max_turns
        self._tasks: dict[str, asyncio.Task] = {}
        self._queues: dict[str, asyncio.Queue] = {}  # thread -> 人类插话队列（S4.4b 消费）

    async def handle_inbound(self, group_key: str, text: str) -> dict:
        """收到一条群人类消息：进行中→插话入队；空闲→起新讨论（后台跑）。"""
        thread = canonical_thread(group_key)
        task = self._tasks.get(thread)
        if task is not None and not task.done():
            self._queues[thread].put_nowait(text)  # 进行中：插话（S4.4b 在 human_gate 消费）
            return {"status": "interjected", "thread": thread}
        roster = await self._roster_provider()
        if not roster:
            return {"status": "no_roster"}
        self._queues[thread] = asyncio.Queue()
        self._tasks[thread] = asyncio.create_task(
            self._run(thread, group_key, roster, text)
        )
        return {"status": "started", "thread": thread, "roster": roster}

    async def _run(self, thread: str, group_key: str, roster: list[str], topic: str) -> None:
        cfg = {"configurable": {"thread_id": thread}}
        stream_input = {
            "group_key": thread,
            "roster": [AgentSlot(contact_id=c) for c in roster],
            "history": [Msg(sender_id="human", sender_kind="human", text=topic)],
            "pending_human": None,
            "max_turns_per_human": self._max_turns,
        }
        try:
            # 后台循环：跑一段 astream（到下一个 human_gate）→ 每轮发言经 bot 推群 →
            # 若图未到 END（snap.next 非空）则 resume 续轮（可带人类插话改向）。
            while True:
                async for ev in iter_events(self._graph, stream_input, cfg):
                    if isinstance(ev, TurnDone):
                        await self._push_turn(group_key, ev)
                    # Output(主笔综合) 暂不推群；Delta/Framed/Interrupt telegram 忽略。
                snap = await self._graph.aget_state(cfg)
                if not snap.next:  # 跑到 END（synthesize）→ 本场结束
                    break
                stream_input = Command(resume=self._next_resume(thread))
        except Exception as e:  # noqa: BLE001
            logger.error(f"relay 讨论 {thread} 异常：{e}")

    def _next_resume(self, thread: str) -> dict:
        """每轮 human_gate 处：队列里有人类插话则带上（→ 消费、预算归零、改向），否则继续。"""
        q = self._queues.get(thread)
        if q is not None and not q.empty():
            try:
                return {"interject": q.get_nowait()}
            except asyncio.QueueEmpty:
                pass
        return {"interject": None}

    async def _push_turn(self, group_key: str, ev: TurnDone) -> None:
        """把一轮发言经对应 bot 推回群（空发言跳过——kimi 可能只出 reasoning）。"""
        if not (ev.text or "").strip():
            logger.warning(f"relay 跳过空发言（{ev.contact_id}）——模型可能只出了 reasoning")
            return
        try:
            await self._outbound.speak(group_key, ev.contact_id, ev.text)
        except Exception as e:  # noqa: BLE001
            logger.error(f"relay 出站推送失败（{ev.contact_id}）：{e}")
