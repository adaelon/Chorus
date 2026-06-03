"""S3.1b 判据：context 投影器——远场只出点、近场出原文；claims 空时退化为原文窗口（§6.11）。"""

from __future__ import annotations

from app.context import make_context_projector
from app.state import Claim, Msg


def _history(n: int) -> list[Msg]:
    return [Msg(sender_id=f"s{i}", sender_kind="ai", text=f"原文{i}") for i in range(n)]


def test_far_points_near_raw_split():
    project = make_context_projector(near=2)
    history = _history(5)  # 原文0..4
    claims = [Claim(speaker_id="A", text="主张X", turn=0), Claim(speaker_id="B", text="主张Y", turn=1)]
    msgs = project(history, claims)
    far, near = msgs[0].content, msgs[1].content

    # 远场：全部点、带归属，不含原文
    assert "A：主张X" in far and "B：主张Y" in far
    assert "原文" not in far
    # 近场：仅最近 near=2 条原文（原文3、原文4），更早的不在原文里
    assert "原文3" in near and "原文4" in near
    assert "原文0" not in near and "原文2" not in near


def test_empty_claims_degrades_to_raw_window():
    project = make_context_projector(near=3)
    history = _history(4)
    msgs = project(history, [])
    # 无点账本 → 只有近场原文窗口一段（等价旧的原文窗口行为）
    assert len(msgs) == 1
    assert "原文1" in msgs[0].content and "原文3" in msgs[0].content
    assert "原文0" not in msgs[0].content  # 窗口外


def test_empty_everything_is_no_context():
    project = make_context_projector()
    assert project([], []) == []


def test_far_covers_whole_run_regardless_of_window():
    # 点账本是远场全程可见：即便发言早已滑出近场窗口，其"点"仍在
    project = make_context_projector(near=1)
    history = _history(10)
    claims = [Claim(speaker_id="A", text="第一轮的主张", turn=0)]
    msgs = project(history, claims)
    assert any("第一轮的主张" in m.content for m in msgs)  # 远场仍见
    assert "原文9" in msgs[-1].content and "原文0" not in msgs[-1].content  # 近场只剩最后 1 条
