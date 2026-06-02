"""S5.2: auto 配方——L3 主持人逐步组原语（§6.13）。

不是手写死的拓扑，而是单循环：主持人(PLAN)每步选下一个原语，引擎 dispatch 后回 PLAN。

    START → FRAME → PLAN ─[next_decision]→ FANOUT | TURN(speak) → PLAN … → SYNTHESIZE → END
                      │
        条件边读 state.next_decision：
          fanout     → fanout（并行候选）→ 回 plan
          speak      → turn（单人发言，PLAN 已设 next_speaker）→ 回 plan
          synthesize → synthesize（收尾）→ END
          stop       → 同 synthesize（出产出再 END）

复用已有原语（frame/fanout/turn/synthesize_roundtable，零改），与圆桌/扇出共享同一原语集——
加一种"现编策略"的协作 = 加一个配方文件，§6.6 抽象再次成立。FRAME 作一次性 setup（分维度）。
**安全**：PLAN 的步数闸（§B2）保证有限步内必到 SYNTHESIZE→END，不会无限循环。
AskHuman/Curate（人在环原语）框架可扩展（加 union 成员 + 节点 + 边），本刀先做自治四原语。
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .nodes.fanout import fanout
from .nodes.frame import AssignFn, frame
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.plan import PlanFn, plan
from .nodes.synthesize import ComposeFn, synthesize_roundtable
from .nodes.turn import turn
from .state import GroupState

_PLAN_ROUTES = {
    "fanout": "fanout",
    "speak": "turn",
    "synthesize": "synthesize",
    "stop": "synthesize",
}


def _route_after_plan(state: GroupState) -> str:
    return _PLAN_ROUTES.get(state.next_decision or "synthesize", "synthesize")


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
    """auto 配方整图：FRAME→(PLAN⇄{FANOUT|TURN})*→SYNTHESIZE。节点依赖可注入以离线测。"""
    g = StateGraph(GroupState)
    g.add_node("frame", partial(frame, assign=assign))
    g.add_node("plan", partial(plan, planner=planner))
    g.add_node("fanout", partial(fanout, generate=generate, persona_provider=persona_provider))
    g.add_node(
        "turn",
        partial(turn, generate=generate, persona_provider=persona_provider, extract=extract),
    )
    g.add_node("synthesize", partial(synthesize_roundtable, compose=compose))

    g.add_edge(START, "frame")
    g.add_edge("frame", "plan")
    g.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"fanout": "fanout", "turn": "turn", "synthesize": "synthesize"},
    )
    g.add_edge("fanout", "plan")
    g.add_edge("turn", "plan")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)
