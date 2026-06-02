"""S3.1b: context 投影器——把记忆(history+点账本)投影成 prompt 里的上下文消息。

圆桌长讨论的"能看多远"= **全程可见但分辨率随距离衰减**（§6.11）：
  远场：全部 `claims`（带归属、极精简）—— 永不丢，圆桌交锋的坐标
  近场：最近 K 轮 `history` 原文 —— 即时语气/细节高清

投影器是**可插拔注入点**（替换 generate.py 里写死的 `history[-10:]`）：窗口、摘要、
点账本都是它的不同实现。默认实现 = 远场点 + 近场原文窗口。
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import BaseMessage, SystemMessage

from .state import Claim, Msg

# 投影器签名：(history, claims) -> 一组上下文 SystemMessage（嵌进发言 prompt）。
ContextProjector = Callable[[list[Msg], list[Claim]], list[BaseMessage]]

NEAR_DEFAULT = 6  # 近场保留的最近原文消息条数（≈3 轮一来一回）


def _far_message(claims: list[Claim]) -> SystemMessage | None:
    if not claims:
        return None
    lines = "\n".join(f"- {c.speaker_id}：{c.text}" for c in claims)
    return SystemMessage(content=f"讨论要点（截至目前，带归属）：\n{lines}")


def _near_message(history: list[Msg], near: int) -> SystemMessage | None:
    recent = (history or [])[-near:]
    if not recent:
        return None
    joined = "\n".join(f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in recent)
    return SystemMessage(content=f"最近发言（原文）：\n{joined}")


def make_context_projector(near: int = NEAR_DEFAULT) -> ContextProjector:
    """造一个分层投影器：远场全部点 + 近场最近 `near` 条原文。

    `claims` 为空时退化为纯原文窗口（行为兼容 S3.1b 之前的现状）。
    """

    def project(history: list[Msg], claims: list[Claim]) -> list[BaseMessage]:
        msgs: list[BaseMessage] = []
        far = _far_message(claims or [])
        if far is not None:
            msgs.append(far)
        near_msg = _near_message(history or [], near)
        if near_msg is not None:
            msgs.append(near_msg)
        return msgs

    return project


# 模块级默认投影器（generate.py 默认用它）。
default_context_projector: ContextProjector = make_context_projector()
