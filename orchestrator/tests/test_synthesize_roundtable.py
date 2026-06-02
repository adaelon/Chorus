"""S3.6b 判据：圆桌专用 SYNTHESIZE 变体——主笔综合点账本，补 S3.3 遗留的空串。

确定性兜底（compose=None）按 speaker 归并 claims；无 claims 退化为 ai 发言原文；
注入 compose 则用主笔产出。全程离线（不碰真实 LLM）。
"""

from __future__ import annotations

from app.nodes.synthesize import synthesize_roundtable
from app.state import Claim, GroupState, Msg


def _state(**kw) -> GroupState:
    return GroupState(group_key="g", **kw)


async def test_fallback_groups_claims_by_speaker():
    state = _state(
        claims=[
            Claim(speaker_id="A", text="点A1", turn=1),
            Claim(speaker_id="B", text="点B1", turn=2),
            Claim(speaker_id="A", text="点A2", turn=3),
        ]
    )
    out = await synthesize_roundtable(state)
    output = out["output"]
    assert output  # 不再是空串（S3.3 遗留已补）
    # A 的两点归并在一起、带归属；B 单独一行
    assert "[A]" in output and "点A1" in output and "点A2" in output
    assert "[B]" in output and "点B1" in output


async def test_fallback_uses_ai_history_when_no_claims():
    state = _state(
        history=[
            Msg(sender_id="u", sender_kind="human", text="议题"),
            Msg(sender_id="A", sender_kind="ai", text="A 的发言"),
        ]
    )
    out = await synthesize_roundtable(state)
    assert "A 的发言" in out["output"]


async def test_injected_composer_is_used():
    async def fake_compose(state: GroupState) -> str:
        return f"主笔综合：{len(state.claims)} 个要点"

    state = _state(claims=[Claim(speaker_id="A", text="x", turn=1)])
    out = await synthesize_roundtable(state, compose=fake_compose)
    assert out["output"] == "主笔综合：1 个要点"
