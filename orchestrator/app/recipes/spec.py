"""S5.4.0a: 原语规格表（PrimitiveSpec）+ 注册表（REGISTRY）——L4 配方库的字母表（§6.16）。

把每个**用户可见原语**的契约从"读源码才知道"变成"读数据"：reads/writes/needs/emits/budget。
这一份 spec 后续一处定义、三处复用——编译器（S5.4.1）按它装配/校验、L3 planner（S5.5）按它
选原语、L4 画布（S5.4.3）按它建卡片/连线合法性。

三态（§6.16，维度一 1.3）：
  transform  纯 state→state，单出边               （frame/fanout/turn/synthesize）
  router     state→next_decision，多出边（条件边） （schedule/plan）——带 budget 防死循环
  human      interrupt 暂停等人，恢复后→state      （clarify/human_gate/curate_gate）

**本刀只登记，不改行为**：node 指向现有原语函数（依赖在编译期 partial 注入，S5.4.1b）。
`human_gate`/`curate_gate` 现仍用 `Command(goto)` 自路由，其 `emits` 是 L4 目标契约——
路由出节点的重构在 S5.4.0b/0c，本刀不动节点代码。`extract`/`generate`/纯 `curate` 是
`turn`/`fanout`/`curate_gate` 肚里的子函数，不入注册表。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..budget import Budget
from ..nodes.clarify import clarify
from ..nodes.curate import curate_interrupt_node
from ..nodes.fanout import fanout
from ..nodes.frame import frame
from ..nodes.human import human_gate
from ..nodes.plan import PLAN_BUDGET, plan
from ..nodes.schedule import SCHEDULE_BUDGET, schedule
from ..nodes.synthesize import produce, synthesize
from ..nodes.turn import turn
from ..state import GroupState

Kind = Literal["transform", "router", "human"]

# 校验用：所有 reads/writes/needs/budget 引用的字段必须是 GroupState 的真字段。
STATE_FIELDS: frozenset[str] = frozenset(GroupState.model_fields)


@dataclass(frozen=True)
class PrimitiveSpec:
    """一个原语的机读契约（§6.16）。reads/writes 算连得上连不上；needs 是硬前置。"""

    name: str
    kind: Kind
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()  # reads 的子集，但必须被上游写过否则非法（编译期 S5.4.1c 校验）
    emits: tuple[str, ...] = ()  # router/human 才有：可能的 next_decision 标签（供条件边）
    args: type | None = None  # 节点级配置 schema（阈值/上限/...，本刀全 None，留后）
    budget: Budget | None = None  # router：声明式预算闸（计数/上限/原因），编译器据此插闸（§6.16 A.4）


@dataclass(frozen=True)
class Primitive:
    """注册表条目：契约 + 实现。node 是裸节点函数，依赖在编译期 partial 注入（S5.4.1b）。"""

    spec: PrimitiveSpec
    node: Callable[..., Any]


def _p(node: Callable[..., Any], **spec_kw: Any) -> Primitive:
    return Primitive(spec=PrimitiveSpec(**spec_kw), node=node)


# 用户可见的 9 个原语（§6.16 "~9 个"）。
REGISTRY: dict[str, Primitive] = {
    "clarify": _p(
        clarify,
        name="clarify",
        kind="human",
        reads=("history",),
        writes=("history",),
        needs=("history",),  # 需求在 history 开场 human 消息里
    ),
    "frame": _p(
        frame,
        name="frame",
        kind="transform",
        reads=("history", "roster"),
        writes=("roster",),
        needs=("history", "roster"),
    ),
    "fanout": _p(
        fanout,
        name="fanout",
        kind="transform",
        reads=("roster", "history", "claims"),
        writes=("candidates",),
        needs=("roster",),
    ),
    "turn": _p(
        turn,
        name="turn",
        kind="transform",
        reads=("next_speaker", "directed_active", "roster", "history", "claims"),
        writes=("history", "turns_since_human", "claims"),
        needs=("next_speaker",),  # 上游 router 必须先定下发言人
    ),
    "schedule": _p(
        schedule,
        name="schedule",
        kind="router",
        reads=("pending_human", "turns_since_human", "max_turns_per_human", "roster", "claims", "history", "directed_queue"),
        writes=("next_speaker", "next_decision", "stop_reason", "directed_queue", "directed_active"),
        needs=("roster",),
        emits=("next_speaker", "yield_to_human", "stop"),
        budget=SCHEDULE_BUDGET,
    ),
    "plan": _p(
        plan,
        name="plan",
        kind="router",
        reads=("plan_steps", "max_plan_steps", "candidates", "roster", "claims", "history"),
        writes=("next_decision", "next_speaker", "plan_steps", "stop_reason"),
        needs=("roster",),
        emits=("fanout", "speak", "synthesize", "stop"),
        budget=PLAN_BUDGET,
    ),
    "human_gate": _p(
        human_gate,
        name="human_gate",
        kind="human",
        reads=("pending_human", "history", "turns_since_human"),
        writes=("history", "pending_human", "turns_since_human", "next_decision", "directed_queue"),
        emits=("continue", "end"),  # 目标契约；路由出节点在 S5.4.0b
    ),
    "curate_gate": _p(
        curate_interrupt_node,
        name="curate_gate",
        kind="human",
        reads=("candidates", "picked", "roster", "history", "claims"),
        writes=("candidates", "picked", "next_decision"),
        needs=("candidates",),
        emits=("curate", "synthesize"),  # 目标契约；路由出节点在 S5.4.0c
    ),
    "synthesize": _p(
        synthesize,
        name="synthesize",
        kind="transform",
        reads=("claims", "history", "picked", "candidates"),
        writes=("output",),
    ),  # S5.4.0e 已合一：按 compose/claims/candidates 分流（圆桌主笔 / 扇出汇候选 / 兜底）
    "produce": _p(
        produce,
        name="produce",
        kind="transform",
        reads=("claims", "history", "picked", "candidates"),
        writes=("output",),
    ),  # S10a 出产物（§6.21）：把 task 当生产任务书交付产物，与 synthesize（出结论）同形、异脑
}


def check_spec(spec: PrimitiveSpec) -> None:
    """单条 spec 自洽校验（违反即 raise，供 test/编译器复用——§B2 确定性裁决）。"""
    if spec.kind not in ("transform", "router", "human"):
        raise ValueError(f"{spec.name}: 非法 kind {spec.kind!r}")
    bad = (set(spec.reads) | set(spec.writes) | set(spec.needs)) - STATE_FIELDS
    if bad:
        raise ValueError(f"{spec.name}: reads/writes/needs 引用了非 GroupState 字段 {sorted(bad)}")
    if not set(spec.needs) <= set(spec.reads):
        raise ValueError(f"{spec.name}: needs 必须是 reads 的子集（{set(spec.needs) - set(spec.reads)} 不在 reads）")
    if spec.emits and spec.kind == "transform":
        raise ValueError(f"{spec.name}: transform 不应有 emits（路由是 router/human 的事）")
    if spec.kind == "router" and not spec.emits:
        raise ValueError(f"{spec.name}: router 必须有 emits（要分支）")
    if spec.emits and "next_decision" not in spec.writes:
        raise ValueError(f"{spec.name}: 有 emits 就必须写 next_decision（条件边读它）")
    if spec.budget is not None:
        if spec.kind != "router":
            raise ValueError(f"{spec.name}: 只有 router 能声明 budget")
        if {spec.budget.count, spec.budget.limit} - STATE_FIELDS:
            raise ValueError(f"{spec.name}: budget 的计数/上限必须是 GroupState 字段")


def validate_registry(registry: dict[str, Primitive] = REGISTRY) -> None:
    """整表自洽：key==name、每条 spec 自洽、node 可调用（违反即 raise）。"""
    for key, prim in registry.items():
        if key != prim.spec.name:
            raise ValueError(f"注册表 key {key!r} 与 spec.name {prim.spec.name!r} 不一致")
        if not callable(prim.node):
            raise ValueError(f"{key}: node 不可调用")
        check_spec(prim.spec)
