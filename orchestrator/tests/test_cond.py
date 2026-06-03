"""S5.4.1a 判据：边条件小解释器（§6.16 C）——白名单算子/字段、all/any 嵌套、非法即 raise。

纯求值，不碰 LLM/图。穷举算子 + 复合 + 各类坏 cond。
"""

from __future__ import annotations

import pytest

from app.recipes_cond import eval_cond
from app.state import AgentSlot, GroupState, Msg


def _state(**kw) -> GroupState:
    base = dict(
        group_key="g",
        roster=[AgentSlot(contact_id="A")],
        history=[Msg(sender_id="u", sender_kind="human", text="q")],
    )
    base.update(kw)
    return GroupState(**base)


def test_numeric_comparisons():
    st = _state(turns_since_human=3)
    assert eval_cond({"field": "turns_since_human", "op": "==", "value": 3}, st)
    assert eval_cond({"field": "turns_since_human", "op": "!=", "value": 4}, st)
    assert eval_cond({"field": "turns_since_human", "op": ">", "value": 2}, st)
    assert eval_cond({"field": "turns_since_human", "op": ">=", "value": 3}, st)
    assert eval_cond({"field": "turns_since_human", "op": "<", "value": 4}, st)
    assert eval_cond({"field": "turns_since_human", "op": "<=", "value": 3}, st)
    assert not eval_cond({"field": "turns_since_human", "op": ">", "value": 3}, st)


def test_string_equality_on_next_decision():
    st = _state(next_decision="speak")
    assert eval_cond({"field": "next_decision", "op": "==", "value": "speak"}, st)
    assert not eval_cond({"field": "next_decision", "op": "==", "value": "stop"}, st)


def test_in_operator():
    st = _state(next_decision="fanout")
    assert eval_cond({"field": "next_decision", "op": "in", "value": ["fanout", "speak"]}, st)
    assert not eval_cond({"field": "next_decision", "op": "in", "value": ["stop"]}, st)


def test_empty_and_truthy():
    st = _state(next_speaker=None, output="结论")
    assert eval_cond({"field": "next_speaker", "op": "empty"}, st)  # None → empty 真
    assert not eval_cond({"field": "next_speaker", "op": "truthy"}, st)
    assert eval_cond({"field": "output", "op": "truthy"}, st)
    assert not eval_cond({"field": "output", "op": "empty"}, st)


def test_all_compound():
    st = _state(turns_since_human=5, next_decision="stop")
    cond = {"all": [
        {"field": "turns_since_human", "op": ">=", "value": 5},
        {"field": "next_decision", "op": "==", "value": "stop"},
    ]}
    assert eval_cond(cond, st)
    assert not eval_cond({"all": [*cond["all"], {"field": "output", "op": "truthy"}]}, st)


def test_any_compound():
    st = _state(next_decision="speak")
    cond = {"any": [
        {"field": "next_decision", "op": "==", "value": "stop"},
        {"field": "next_decision", "op": "==", "value": "speak"},
    ]}
    assert eval_cond(cond, st)


def test_nested_compound():
    st = _state(turns_since_human=2, next_decision="speak")
    cond = {"any": [
        {"field": "next_decision", "op": "==", "value": "stop"},
        {"all": [
            {"field": "next_decision", "op": "==", "value": "speak"},
            {"field": "turns_since_human", "op": "<", "value": 6},
        ]},
    ]}
    assert eval_cond(cond, st)


def test_rejects_unknown_field():
    with pytest.raises(ValueError, match="未知 state 字段"):
        eval_cond({"field": "not_a_field", "op": "==", "value": 1}, _state())


def test_rejects_unknown_op():
    with pytest.raises(ValueError, match="未知算子"):
        eval_cond({"field": "turns_since_human", "op": "~=", "value": 1}, _state())


def test_rejects_non_dict():
    with pytest.raises(ValueError, match="条件必须是 dict"):
        eval_cond("turns_since_human > 3", _state())


def test_rejects_malformed_compound():
    with pytest.raises(ValueError, match="all 必须是条件列表"):
        eval_cond({"all": {"field": "turns_since_human", "op": "==", "value": 1}}, _state())
