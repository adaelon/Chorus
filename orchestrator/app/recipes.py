"""配方(recipe)装配：把原语节点接成一张 LangGraph 图。

扇出配方（S3.0 起）是**一张完整的图**：

    CLARIFY → FRAME → FANOUT → [CURATE ⇄ (interrupt 循环)] → SYNTHESIZE → END

CURATE 用 LangGraph `interrupt` 做人在环（暂停—等人 resume—多轮），SYNTHESIZE 是
图的终端节点。service 层只负责"起图 / resume + 转发 interrupt payload"，引擎无 if/else
特例（模型 A，§6.10）。节点的 LLM 依赖（assign/generate/信誉）可注入，便于离线测试。
圆桌配方（S3.1+）将用同一组原语、另一种接线，复用同一套装配/运行/打断机制。
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .nodes.clarify import ClarifyFn, clarify
from .nodes.curate import ReputationAdjuster, curate_interrupt_node
from .nodes.fanout import fanout
from .nodes.frame import AssignFn, frame
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.synthesize import synthesize
from .state import GroupState


def build_fanout_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
    clarify_assess: ClarifyFn | None = None,
):
    """扇出配方整图：CLARIFY→FRAME→FANOUT→CURATE(interrupt 循环)→SYNTHESIZE。"""
    g = StateGraph(GroupState)
    g.add_node("clarify", partial(clarify, assess=clarify_assess))
    g.add_node("frame", partial(frame, assign=assign))
    g.add_node("fanout", partial(fanout, generate=generate, persona_provider=persona_provider))
    g.add_node(
        "curate",
        partial(
            curate_interrupt_node,
            generate=generate,
            persona_provider=persona_provider,
            reputation_adjuster=reputation_adjuster,
        ),
        destinations=("curate", "synthesize"),
    )
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "clarify")
    g.add_edge("clarify", "frame")
    g.add_edge("frame", "fanout")
    g.add_edge("fanout", "curate")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)
