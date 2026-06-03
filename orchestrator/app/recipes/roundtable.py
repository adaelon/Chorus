"""圆桌配方装配（S3.3 / S5.4.1d）——§6.6 抽象的验收点，现以声明式 JSON 兑现。

用**已有原语**（clarify/frame/schedule/turn/human_gate/synthesize，零改）接成另一种图，
拓扑是 `recipes_builtin` 的数据（非手写 StateGraph），经 `compile_recipe` 直译：

    CLARIFY → FRAME → SCHEDULE ─[next_decision]→ TURN → SCHEDULE … → SYNTHESIZE → END

`human_in_loop=True`（S3.4）：用 `ROUNDTABLE`（每轮发言后过 `human_gate` interrupt，真人可
插话/改向/收尾）；否则用 `ROUNDTABLE_CONTINUOUS`（自动连续讨论，到预算闸/停）。

与扇出/auto 共享同一套节点与编译/运行机制——加一种协作模式 = 加一份配方数据（§6.6）。

**入口约定**：初始人类 request 作为 `history` 的开场 human 消息传入、`pending_human=None`；
故 SCHEDULE 第一步不会因 pending_human 而让位（讨论中真人插话才注入 pending_human，S3.4）。
"""

from __future__ import annotations

from ..nodes.clarify import ClarifyFn
from ..nodes.extract import ClaimExtractor
from ..nodes.frame import AssignFn
from ..nodes.generate import GenerateFn, PersonaProvider
from ..nodes.schedule import PickFn
from ..nodes.synthesize import ComposeFn
from .builtin import ROUNDTABLE, ROUNDTABLE_CONTINUOUS
from .compile import compile_recipe


def build_roundtable_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    extract: ClaimExtractor | None = None,
    pick: PickFn | None = None,
    clarify_assess: ClarifyFn | None = None,
    compose: ComposeFn | None = None,
    human_in_loop: bool = False,
):
    """圆桌配方整图：编译 `ROUNDTABLE`(人在环) 或 `ROUNDTABLE_CONTINUOUS`(自动连续)。"""
    deps = {
        "assign": assign,
        "generate": generate,
        "persona_provider": persona_provider,
        "extract": extract,
        "pick": pick,
        "compose": compose,
        "assess": clarify_assess,  # clarify 节点形参名是 assess
    }
    recipe = ROUNDTABLE if human_in_loop else ROUNDTABLE_CONTINUOUS
    return compile_recipe(recipe, checkpointer, deps=deps)
