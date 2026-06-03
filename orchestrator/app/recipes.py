"""配方(recipe)装配：把声明式 JSON 编译成 LangGraph 图（S5.4.1d，§6.16 C）。

扇出配方不再手写拓扑，而是 `recipes_builtin.FANOUT` 数据经 `compile_recipe` 直译：

    CLARIFY → FRAME → FANOUT → [CURATE ⇄ (interrupt 循环)] → SYNTHESIZE → END

CURATE 用 LangGraph `interrupt` 做人在环（暂停—等人 resume—多轮，self-loop 在 JSON 里），
SYNTHESIZE 是终端节点。本函数只负责把注入的 LLM 依赖组成 deps（节点形参名）交给编译器，
编译器按节点签名过滤注入、据 spec 插闸。圆桌/auto 同理（recipes_roundtable/recipes_auto）。
"""

from __future__ import annotations

from .nodes.clarify import ClarifyFn
from .nodes.curate import ReputationAdjuster
from .nodes.frame import AssignFn
from .nodes.generate import GenerateFn, PersonaProvider
from .recipes_builtin import FANOUT
from .recipes_compile import compile_recipe


def build_fanout_recipe(
    checkpointer,
    *,
    assign: AssignFn | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    reputation_adjuster: ReputationAdjuster | None = None,
    clarify_assess: ClarifyFn | None = None,
):
    """扇出配方整图：编译 `recipes_builtin.FANOUT`（CLARIFY→FRAME→FANOUT→CURATE⇄→SYNTHESIZE）。"""
    deps = {
        "assign": assign,
        "generate": generate,
        "persona_provider": persona_provider,
        "reputation_adjuster": reputation_adjuster,
        "assess": clarify_assess,  # clarify 节点形参名是 assess
    }
    return compile_recipe(FANOUT, checkpointer, deps=deps)
