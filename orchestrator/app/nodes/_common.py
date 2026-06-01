"""节点共用的小工具。"""

from __future__ import annotations

from ..state import GroupState


def request_text(state: GroupState) -> str:
    """取触发本轮的人类需求：优先 pending_human，否则历史里最近一条人类消息。"""
    if state.pending_human is not None:
        return state.pending_human.text
    for msg in reversed(state.history):
        if msg.sender_kind == "human":
            return msg.text
    return ""
