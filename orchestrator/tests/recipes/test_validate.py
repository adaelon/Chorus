"""S5.4.1c 判据：validate_recipe 四关 + 结构前置（§6.16 A.4/C）。

好图返回 []；断前置/缺 else/无闸环/坏 when/未知节点各报对应错（收集全部，断言含目标子串）。
"""

from __future__ import annotations

import pytest

from app.recipes.builtin import AUTO, FANOUT, ROUNDTABLE, ROUNDTABLE_CONTINUOUS
from app.recipes.validate import validate_recipe


@pytest.mark.parametrize("recipe", [FANOUT, ROUNDTABLE, ROUNDTABLE_CONTINUOUS, AUTO])
def test_builtin_recipes_are_valid(recipe):
    """四个内置配方都必须过校验（环上有闸/needs 可达/必有 else）。"""
    assert validate_recipe(recipe) == []


def _good() -> dict:
    """圆桌形状：schedule(带闸 router) 条件边+else，turn→schedule 成环（环上有闸）。"""
    return {
        "recipe": "rt", "version": 1,
        "nodes": [
            {"id": "frame", "use": "frame"},
            {"id": "schedule", "use": "schedule"},
            {"id": "turn", "use": "turn"},
            {"id": "synthesize", "use": "synthesize"},
        ],
        "edges": [
            {"from": "START", "to": "frame"},
            {"from": "frame", "to": "schedule"},
            {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
            {"from": "schedule", "to": "synthesize"},
            {"from": "turn", "to": "schedule"},
            {"from": "synthesize", "to": "END"},
        ],
    }


def test_good_recipe_passes():
    assert validate_recipe(_good()) == []


def test_missing_else():
    r = _good()
    # 删掉 schedule 的 else 边（只剩 when 边）
    r["edges"] = [e for e in r["edges"] if not (e["from"] == "schedule" and "when" not in e)]
    errs = validate_recipe(r)
    assert any("缺 else" in m for m in errs)


def test_needs_unmet():
    # frame→turn 直连，turn 之前无 router 写 next_speaker
    r = {
        "recipe": "bad", "version": 1,
        "nodes": [
            {"id": "frame", "use": "frame"},
            {"id": "turn", "use": "turn"},
            {"id": "synthesize", "use": "synthesize"},
        ],
        "edges": [
            {"from": "START", "to": "frame"},
            {"from": "frame", "to": "turn"},
            {"from": "turn", "to": "synthesize"},
            {"from": "synthesize", "to": "END"},
        ],
    }
    errs = validate_recipe(r)
    assert any("next_speaker" in m and "needs 不满足" in m for m in errs)


def test_cycle_without_budget():
    # frame⇄turn 成环，二者都不带 budget
    r = {
        "recipe": "bad", "version": 1,
        "nodes": [
            {"id": "frame", "use": "frame"},
            {"id": "turn", "use": "turn"},
        ],
        "edges": [
            {"from": "START", "to": "frame"},
            {"from": "frame", "to": "turn"},
            {"from": "turn", "to": "frame"},
        ],
    }
    errs = validate_recipe(r)
    assert any("无闸的环" in m for m in errs)


def test_bad_when_field():
    r = _good()
    for e in r["edges"]:
        if e["from"] == "schedule" and "when" in e:
            e["when"] = {"field": "not_a_field", "op": "==", "value": 1}
    errs = validate_recipe(r)
    assert any("条件非法" in m for m in errs)


def test_unknown_node_in_edge():
    r = _good()
    r["edges"].append({"from": "schedule", "to": "ghost"})
    errs = validate_recipe(r)
    assert any("未知节点" in m for m in errs)


def test_unregistered_primitive():
    r = _good()
    r["nodes"].append({"id": "x", "use": "nope"})
    errs = validate_recipe(r)
    assert any("未注册原语" in m for m in errs)
