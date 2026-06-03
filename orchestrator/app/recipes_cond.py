"""S5.4.1a: 边条件小解释器（§6.16 C 定论）——L4 DAG 的 `when` 求值。

边的条件是**数据不是代码**：`eval_cond(cond, state) -> bool`，由白名单算子在白名单 state 字段上求值，
**无 `eval`/无任意代码**——这是声明式 DAG 比"让用户写 Python"安全的根（用户只能组合、填值）。

文法（可扩展，本刀实现原子 + all/any，复合可嵌套）：

    cond = {"field": <GroupState 字段>, "op": <算子>, "value": <字面量>}   # 原子
         | {"all": [cond, ...]}                                          # 全真
         | {"any": [cond, ...]}                                          # 任一真

算子白名单：==  !=  >  >=  <  <=  in（左值 ∈ value）  empty（左值假）  truthy（左值真）。
`empty`/`truthy` 不读 value。字段非 GroupState 真字段 / 算子不在白名单 → 立刻 raise（供 1c 校验复用）。
低层模块（仅依赖 state），编译器（1b）/校验（1c）/画布都能 import 不成环。
"""

from __future__ import annotations

import operator
from typing import Any

from .state import GroupState

STATE_FIELDS: frozenset[str] = frozenset(GroupState.model_fields)

# 算子白名单：(左值, value) -> bool。empty/truthy 忽略 value。
_OPS: dict[str, Any] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "in": lambda a, b: a in b,
    "empty": lambda a, _: not a,
    "truthy": lambda a, _: bool(a),
}


def eval_cond(cond: dict, state: GroupState) -> bool:
    """求值一条边条件（§6.16 C）。复合可嵌套；非法 cond/字段/算子 → ValueError。"""
    if not isinstance(cond, dict):
        raise ValueError(f"条件必须是 dict，得到 {type(cond).__name__}")
    if "all" in cond:
        subs = cond["all"]
        if not isinstance(subs, list):
            raise ValueError("all 必须是条件列表")
        return all(eval_cond(c, state) for c in subs)
    if "any" in cond:
        subs = cond["any"]
        if not isinstance(subs, list):
            raise ValueError("any 必须是条件列表")
        return any(eval_cond(c, state) for c in subs)

    field = cond.get("field")
    op = cond.get("op")
    if field not in STATE_FIELDS:
        raise ValueError(f"未知 state 字段：{field!r}")
    if op not in _OPS:
        raise ValueError(f"未知算子：{op!r}")
    return bool(_OPS[op](getattr(state, field), cond.get("value")))
