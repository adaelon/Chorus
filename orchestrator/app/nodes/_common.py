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


def task_text(state: GroupState) -> str:
    """取开场的原始诉求（产出物"要交付什么"的锚点）：history 里第一条人类消息（§6.21）。

    与 `request_text`（最近一条）相对：produce 要的是"用户最初要什么形态的产物"，
    由开场议题定（后续插话改方向、不改交付物类型）。
    """
    for msg in state.history:
        if msg.sender_kind == "human":
            return msg.text
    return ""
