"""S5.4.0a 判据：原语规格表自洽（§6.16 维度一 A.1）。

纯结构校验，不碰 LLM/图：注册表每条 spec 自洽（needs⊆reads、字段都是 GroupState 真字段、
router 必有 emits、emits⟹写 next_decision、budget 仅 router），坏 spec 必被 check_spec 拒。
"""

from __future__ import annotations

import pytest

from app.budget import Budget, budget_tripped
from app.recipes.spec import REGISTRY, PrimitiveSpec, check_spec, validate_registry
from app.state import AgentSlot, GroupState, Msg


def test_registry_is_self_consistent():
    """整表自洽：key==name、node 可调用、每条 spec 过 check_spec。"""
    validate_registry()


def test_registry_covers_expected_primitives():
    """登记的用户可见原语（extract/generate/纯 curate 不入）；S10a/b 加 produce/deliver。"""
    assert set(REGISTRY) == {
        "clarify", "frame", "fanout", "turn",
        "schedule", "plan", "human_gate", "curate_gate", "synthesize", "produce", "deliver",
    }


def test_spot_contracts():
    """抽查几条关键契约，锁住设计意图。"""
    assert REGISTRY["turn"].spec.needs == ("next_speaker",)  # 发言前必有上游定人
    assert REGISTRY["schedule"].spec.budget == Budget("turns_since_human", "max_turns_per_human", "budget")
    assert REGISTRY["plan"].spec.budget == Budget("plan_steps", "max_plan_steps", "plan_budget")
    # router 的 emits 与节点真实路由标签一致
    assert set(REGISTRY["schedule"].spec.emits) == {"next_speaker", "yield_to_human", "stop"}
    assert set(REGISTRY["plan"].spec.emits) == {"fanout", "speak", "synthesize", "stop"}


def test_budget_descriptor_drives_gate():
    """声明式 Budget：触顶（计数≥上限）由 budget_tripped 判定（§6.16 A.4）。"""
    b = REGISTRY["plan"].spec.budget
    st = GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id="A")],
        history=[Msg(sender_id="u", sender_kind="human", text="q")],
        plan_steps=8,
        max_plan_steps=8,
    )
    assert budget_tripped(st, b) is True
    assert budget_tripped(st.model_copy(update={"plan_steps": 7}), b) is False


def test_every_router_emits_and_writes_next_decision():
    for prim in REGISTRY.values():
        if prim.spec.kind == "router":
            assert prim.spec.emits, f"{prim.spec.name} router 必须有 emits"
            assert "next_decision" in prim.spec.writes


def test_check_spec_rejects_needs_not_subset_of_reads():
    bad = PrimitiveSpec(name="x", kind="transform", reads=("history",), needs=("roster",))
    with pytest.raises(ValueError, match="needs 必须是 reads 的子集"):
        check_spec(bad)


def test_check_spec_rejects_unknown_state_field():
    bad = PrimitiveSpec(name="x", kind="transform", reads=("not_a_field",))
    with pytest.raises(ValueError, match="非 GroupState 字段"):
        check_spec(bad)


def test_check_spec_rejects_transform_with_emits():
    bad = PrimitiveSpec(name="x", kind="transform", writes=("next_decision",), emits=("a",))
    with pytest.raises(ValueError, match="transform 不应有 emits"):
        check_spec(bad)


def test_check_spec_rejects_emits_without_next_decision():
    bad = PrimitiveSpec(name="x", kind="router", writes=("stop_reason",), emits=("a", "b"))
    with pytest.raises(ValueError, match="写 next_decision"):
        check_spec(bad)


def test_check_spec_rejects_router_without_emits():
    bad = PrimitiveSpec(name="x", kind="router", writes=("next_decision",))
    with pytest.raises(ValueError, match="router 必须有 emits"):
        check_spec(bad)


def test_check_spec_rejects_budget_on_non_router():
    bad = PrimitiveSpec(name="x", kind="transform", budget=Budget("turns_since_human", "max_turns_per_human", "budget"))
    with pytest.raises(ValueError, match="只有 router 能声明 budget"):
        check_spec(bad)


def test_check_spec_rejects_budget_with_unknown_field():
    bad = PrimitiveSpec(name="x", kind="router", writes=("next_decision",), emits=("stop",),
                        budget=Budget("not_a_field", "max_plan_steps", "r"))
    with pytest.raises(ValueError, match="budget 的计数/上限必须是 GroupState 字段"):
        check_spec(bad)
