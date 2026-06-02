"""圆桌配方装配（S3.3）——§6.6 抽象的验收点。

用**已有原语**（clarify/frame/schedule/turn/synthesize，零改）接成另一种图：

    CLARIFY → FRAME → SCHEDULE ─[next_decision]→ TURN → SCHEDULE … → SYNTHESIZE → END
                          │
            条件边读 state.next_decision 路由：
              next_speaker  → turn（发言后回 schedule，循环）
              stop          → synthesize（预算闸 / 主持人收尾）
              yield_to_human → synthesize（S3.4 改成 interrupt 打断）

与扇出配方（recipes.py）共享同一套节点与装配/运行机制——加一种协作模式 = 加一个配方
文件，**不动引擎/节点**（§6.6 模式=可组合配方成立）。

**入口约定**：初始人类 request 作为 `history` 的开场 human 消息传入、`pending_human=None`；
故 SCHEDULE 第一步不会因 pending_human 而让位（讨论中真人插话才注入 pending_human，S3.4）。
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .nodes.clarify import ClarifyFn, clarify
from .nodes.extract import ClaimExtractor
from .nodes.frame import AssignFn, frame
from .nodes.generate import GenerateFn, PersonaProvider
from .nodes.human import human_gate
from .nodes.schedule import PickFn, schedule
from .nodes.synthesize import synthesize
from .nodes.turn import turn
from .state import GroupState


def _route_after_schedule(state: GroupState) -> str:
    """条件边：按 SCHEDULE 落下的决策类型选下一步。"""
    return state.next_decision or "stop"


def build_roundtable_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    extract: ClaimExtractor | None = None,
    pick: PickFn | None = None,
    clarify_assess: ClarifyFn | None = None,
    human_in_loop: bool = False,
):
    """圆桌配方整图：CLARIFY→FRAME→(SCHEDULE⇄TURN)*→SYNTHESIZE。节点依赖可注入以离线测试。

    `human_in_loop=True`（S3.4）：每轮发言后插入 `human_gate` interrupt 横切——真人可在
    任意轮插话（让位/改向），复用 S3.0 interrupt 机制。默认关（自动连续讨论，到预算闸/停）。
    """
    g = StateGraph(GroupState)
    g.add_node("clarify", partial(clarify, assess=clarify_assess))
    g.add_node("frame", partial(frame, assign=assign))
    g.add_node("schedule", partial(schedule, pick=pick))
    g.add_node(
        "turn",
        partial(turn, generate=generate, persona_provider=persona_provider, extract=extract),
    )
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "clarify")
    g.add_edge("clarify", "frame")
    g.add_edge("frame", "schedule")

    if human_in_loop:
        g.add_node("human_gate", human_gate, destinations=("schedule",))
        g.add_edge("turn", "human_gate")  # 每轮发言后过打断窗口
        yield_target = "human_gate"
    else:
        g.add_edge("turn", "schedule")
        yield_target = "synthesize"

    g.add_conditional_edges(
        "schedule",
        _route_after_schedule,
        {
            "next_speaker": "turn",
            "stop": "synthesize",
            "yield_to_human": yield_target,  # 人在环时让位走 human_gate，否则收尾
        },
    )
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)
