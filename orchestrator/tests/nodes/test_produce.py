"""S10a 判据：produce 原语（出产物，§6.21）——把原始 task 当生产任务书，交付产物而非纪要。

composer 把开场 task 放进 brief、讨论要点当约束、明确禁止写成会议纪要；produce 节点同 synthesize
形态（transform→output），无 composer 时确定性兜底。注入假 model/compose，离线。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.nodes.synthesize import _fallback_compose, default_produce_composer, produce
from app.state import Claim, GroupState, Msg


class _CapModel:
    """记录看到的 messages 的假 model（astream 流式，仿真实后端）。"""

    def __init__(self, reply: str = "PRODUCT") -> None:
        self.reply = reply
        self.seen = None

    async def astream(self, messages, config=None):  # noqa: ANN001 - 测试桩
        self.seen = messages
        yield AIMessage(content=self.reply)


def _state() -> GroupState:
    return GroupState(
        group_key="g",
        history=[
            Msg(sender_id="u", sender_kind="human", text="帮我写一个 sub-agent 的 prompt"),
            Msg(sender_id="A", sender_kind="ai", text="原始文本绝不污染主线程"),
        ],
        claims=[Claim(speaker_id="A", text="原始文本绝不污染主线程", turn=1)],
    )


async def test_produce_composer_anchors_on_original_task():
    model = _CapModel("【交付】这是一个可直接用的 prompt …")
    out = await default_produce_composer(model)(_state())
    assert out == "【交付】这是一个可直接用的 prompt …"

    blob = "\n".join(str(m.content) for m in model.seen)
    assert "帮我写一个 sub-agent 的 prompt" in blob  # 原始 task 进了 brief
    assert "原始文本绝不污染主线程" in blob  # 讨论要点作为约束/素材
    assert "会议纪要" in blob  # system 明确禁止写成纪要（= 与 synthesize 的关键区别）


async def test_produce_node_uses_injected_composer():
    async def fake(state):
        return "PRODUCED"

    assert await produce(_state(), compose_produce=fake) == {"output": "PRODUCED"}


async def test_produce_node_fallback_without_composer():
    out = await produce(_state())  # 无 composer（离线/未配置）→ 确定性兜底（claims 归并）
    assert out["output"] == _fallback_compose(_state())
    assert "A" in out["output"]  # 兜底按 speaker 归并点账本
