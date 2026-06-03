"""S5.2 / S5.4.1d: auto 配方——L3 主持人逐步组原语（§6.13），以声明式 JSON 兑现。

不是手写死的拓扑，而是单循环：主持人(PLAN)每步选下一个原语，引擎 dispatch 后回 PLAN。
拓扑是 `recipes_builtin.AUTO` 数据，经 `compile_recipe` 直译：

    START → FRAME → PLAN ─[next_decision]→ FANOUT | TURN(speak) → PLAN … → SYNTHESIZE → END
                      │
        条件边读 state.next_decision：
          fanout     → fanout（并行候选）→ 回 plan
          speak      → turn（单人发言，PLAN 已设 next_speaker）→ 回 plan
          else       → synthesize（synthesize/stop 收尾）→ END

复用已有原语（frame/fanout/turn/synthesize，零改），与圆桌/扇出共享同一原语集与编译/运行
机制——加一种"现编策略"的协作 = 加一份配方数据（§6.6）。FRAME 作一次性 setup（分维度）。
**安全**：PLAN 的步数闸（§B2，spec.budget 声明式，编译器自动插）保证有限步内必到 SYNTHESIZE→END。
AskHuman/Curate（人在环原语）可扩展（registry 加成员 + 配方加 nodes/edges），本刀先做自治四原语。
"""

from __future__ import annotations

from ..nodes.frame import AssignFn
from ..nodes.generate import GenerateFn, PersonaProvider
from ..nodes.plan import PlanFn
from ..nodes.synthesize import ComposeFn
from .builtin import AUTO
from .compile import compile_recipe


def build_auto_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    extract=None,
    planner: PlanFn | None = None,
    compose: ComposeFn | None = None,
):
    """auto 配方整图：编译 `recipes_builtin.AUTO`（FRAME→PLAN⇄{FANOUT|TURN}→SYNTHESIZE）。"""
    deps = {
        "assign": assign,
        "generate": generate,
        "persona_provider": persona_provider,
        "extract": extract,
        "planner": planner,
        "compose": compose,
    }
    return compile_recipe(AUTO, checkpointer, deps=deps)
