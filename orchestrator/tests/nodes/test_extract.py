"""S3.1c 判据：中立提点 + 圆桌压缩可见性（§6.11）。

① `default_claim_extractor` 把发言压成带归属的点（注入假 model 走 text_json，离线）；上限 3。
② 集成：连续发言后，最新发言者的 prompt **远场见早期发言的"点"、近场不见其原文**
   （第一轮原文滑出近场窗口，但其点经账本全程可见）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.context import make_context_projector
from app.nodes.extract import default_claim_extractor
from app.nodes.generate import default_generator
from app.nodes.turn import turn
from app.state import AgentSlot, Claim, GroupState, Msg


class _FakeModel:
    """假模型：astream 恒返回预设文本（供 text_json 自解析）。"""

    def __init__(self, content: str) -> None:
        self.content = content

    async def astream(self, messages, config=None):  # noqa: ANN001 - 测试桩
        yield AIMessageChunk(content=self.content)


class _SeqModel:
    """假模型：每次调用产出递增、可区分的内容，并记录看到的 messages。"""

    def __init__(self) -> None:
        self.seen: list[list] = []
        self.i = 0

    async def astream(self, messages, config=None):  # noqa: ANN001 - 测试桩
        self.seen.append(messages)
        self.i += 1
        yield AIMessageChunk(content=f"原文{self.i}")


async def _fake_extract(text: str, speaker_id: str, turn_idx: int) -> list[Claim]:
    return [Claim(speaker_id=speaker_id, text=f"{speaker_id}的点", turn=turn_idx)]


async def test_extractor_makes_attributed_points():
    model = _FakeModel('{"points": ["主张甲", "主张乙"]}')
    extract = default_claim_extractor(model)
    claims = await extract("一大段啰嗦的发言……", "A", 2)
    assert [c.text for c in claims] == ["主张甲", "主张乙"]
    assert all(c.speaker_id == "A" and c.turn == 2 for c in claims)


async def test_extractor_caps_at_three_points():
    model = _FakeModel('{"points": ["1","2","3","4","5"]}')
    extract = default_claim_extractor(model)
    claims = await extract("发言", "B", 1)
    assert len(claims) == 3  # MAX_POINTS


async def test_extractor_empty_text_noop():
    model = _FakeModel('{"points": ["不该被调用"]}')
    extract = default_claim_extractor(model)
    assert await extract("   ", "C", 1) == []


async def test_compression_far_points_visible_near_raw_dropped():
    """圆桌压缩：第三位发言者的 prompt 里，第一轮原文滑出近场，但其点经账本仍可见。"""
    model = _SeqModel()
    gen = default_generator(model, projector=make_context_projector(near=1))
    state = GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id=c) for c in ("A", "B", "C")],
        history=[Msg(sender_id="u", sender_kind="human", text="问题Q")],
        pending_human=Msg(sender_id="u", sender_kind="human", text="问题Q"),
        next_speaker="A",
    )
    out1 = await turn(state, generate=gen, extract=_fake_extract)  # A → "原文1"
    s2 = state.model_copy(update={**out1, "next_speaker": "B"})
    out2 = await turn(s2, generate=gen, extract=_fake_extract)  # B → "原文2"
    s3 = s2.model_copy(update={**out2, "next_speaker": "C"})
    await turn(s3, generate=gen, extract=_fake_extract)  # C 发言，捕获其 prompt

    prompt = "\n".join(m.content for m in model.seen[-1])
    assert "A的点" in prompt  # 远场：A 的点全程可见（即便原文已滑出）
    assert "原文1" not in prompt  # 第一轮原文（A 的发言）滑出近场（near=1）
    assert "原文2" in prompt  # 近场 near=1：上一条（B 的发言原文）仍在
