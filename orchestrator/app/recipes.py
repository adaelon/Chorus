"""配方(recipe)装配：把原语节点接成一张 LangGraph 图。

扇出配方的**自动生成段** = CLARIFY → FRAME → FANOUT。人工策展（CURATE）与
SYNTHESIZE 是其后基于 checkpoint 状态的操作（见 service.py），因人在环、可迭代。
节点的 LLM 依赖（assign/generate）可注入，便于离线测试。
圆桌配方（S3）将用同一组原语、另一种接线，复用同一套装配/运行机制。
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .nodes.clarify import clarify
from .nodes.fanout import fanout
from .nodes.frame import frame
from .nodes.generate import GenerateFn
from .nodes.frame import AssignFn
from .state import GroupState


def build_fanout_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
):
    """CLARIFY → FRAME → FANOUT 的自动生成段，编译挂 checkpointer。"""
    g = StateGraph(GroupState)
    g.add_node("clarify", clarify)
    g.add_node("frame", partial(frame, assign=assign))
    g.add_node("fanout", partial(fanout, generate=generate))
    g.add_edge(START, "clarify")
    g.add_edge("clarify", "frame")
    g.add_edge("frame", "fanout")
    g.add_edge("fanout", END)
    return g.compile(checkpointer=checkpointer)
