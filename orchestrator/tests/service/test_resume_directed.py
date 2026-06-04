"""S9c 判据（后端）：resume 透传 @定向 `directed` 列表到 human_gate（§6.20）。

前端 chips 选的 contact_id 列表经 RoundtableResumeReq.directed → _resume_payload 的 interject
分支带上 `directed`（其余暂停点 skip/answer/end 优先、不带 directed）。
"""

from __future__ import annotations

from app.service import RoundtableResumeReq, _resume_payload


def test_interject_with_directed_passthrough():
    req = RoundtableResumeReq(interject="把方案改激进点", directed=["ada1", "ada2"])
    assert _resume_payload(req) == {"interject": "把方案改激进点", "directed": ["ada1", "ada2"]}


def test_interject_without_directed_unchanged():
    req = RoundtableResumeReq(interject="对全员说一句")
    assert _resume_payload(req) == {"interject": "对全员说一句"}


def test_empty_directed_not_included():
    # 空列表 = 不定向（对全员）→ 不带 directed 键，human_gate 不填队列
    req = RoundtableResumeReq(interject="x", directed=[])
    assert _resume_payload(req) == {"interject": "x"}


def test_other_pause_points_ignore_directed():
    # end/skip/answer 优先于 interject 分支，directed 不应渗入
    assert _resume_payload(RoundtableResumeReq(end=True, directed=["a"])) == {"end": True}
    assert _resume_payload(RoundtableResumeReq(skip=True, directed=["a"])) == {"skip": True}
    assert _resume_payload(RoundtableResumeReq(answer="ans", directed=["a"])) == {"answer": "ans"}
