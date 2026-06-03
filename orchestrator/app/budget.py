"""S5.4.0d: 预算闸描述符——把"防死循环"的字段/原因从散落字面量收敛成声明式 Budget（§6.16 A.4）。

router 原语声明自己的 `Budget(count, limit, reason)`：计数字段 ≥ 上限字段 → 触顶停。
- 节点默认用自身 Budget 常量（`SCHEDULE_BUDGET`/`PLAN_BUDGET`），故**直接调用也照常受闸**；
- `spec.budget` 暴露同一常量供编译器/画布读——L4 用户画的环天生有闸（编译期 S5.4.1c 校验环上必有闸）。

gate 逻辑统一走 `budget_tripped`，节点不再硬编码 `turns_since_human`/`max_plan_steps` 这类字段名。
低层模块（仅依赖 state），故 nodes 与 recipes.spec 都能 import 而不成环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import GroupState


@dataclass(frozen=True)
class Budget:
    """一个 router 的预算闸：计数字段、上限字段、触顶 stop_reason（均为 GroupState 字段名）。"""

    count: str
    limit: str
    reason: str


def budget_tripped(state: GroupState, budget: Budget) -> bool:
    """计数 ≥ 上限 → True（§B2 确定性裁决，防 LLM 跑偏/死循环/烧钱）。"""
    return getattr(state, budget.count) >= getattr(state, budget.limit)
