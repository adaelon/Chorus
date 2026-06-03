"""S5.5: L3——AI 按任务产出一张 recipe DAG（§6.16）。

§B2：AI 只做**结构化高层选择**（mode/clarify/human_in_loop），确定性 `assemble_recipe` 据此
拼出**保证合法**的图——AI 不裸写 nodes/edges（易出非法/死循环图）。产物是可存库、可在画布渲成
卡片流、可 `/recipe/run` 跑的图工件（"AI 现编"= 一张看得懂、可改的图，而非黑箱运行时即兴）。

vocabulary 先支持 roundtable/fanout + clarify 开关 + 人在环开关，assemble 由内置模板裁出；
更丰富的"分阶段组合"（先 fanout 发散再 discuss 收敛…）可在 assemble 里扩展（registry/builtin 不变）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..structured import structured_invoke
from .builtin import FANOUT, ROUNDTABLE, ROUNDTABLE_CONTINUOUS
from .validate import validate_recipe

_MODES = ("roundtable", "fanout")
_MODE_ZH = {"roundtable": "圆桌", "fanout": "扇出策展"}


class RecipePlan(BaseModel):
    """AI 对一个任务的高层协作选择（§B2 的"提议"侧）。"""

    mode: str = "roundtable"  # roundtable（讨论收敛）| fanout（并行候选+策展）
    clarify: bool = True  # 先澄清需求？
    human_in_loop: bool = True  # roundtable：每轮让真人把关？
    reason: str = ""


# (task, roster) -> RecipePlan。默认 LLM；可注入假实现离线测。
RecipePlanner = Callable[[str, list[str]], Awaitable[RecipePlan]]


def default_recipe_planner(model: ChatOpenAI) -> RecipePlanner:
    """主持人 LLM 选高层协作形态，复用 structured_invoke（§6.9）。"""

    async def planner(task: str, roster: list[str]) -> RecipePlan:
        system = (
            "你是协作设计师。按任务选最合适的协作形态：\n"
            "- mode=roundtable：多人轮流讨论、逐步收敛（适合议题/决策/开放讨论）。\n"
            "- mode=fanout：多人并行各出一版候选，再人工策展汇总（适合要多个方案/创作初稿）。\n"
            "- clarify：需求是否模糊到需先澄清一问。\n"
            "- human_in_loop：圆桌是否每轮让真人把关/插话（高风险或需把控时开）。\n"
            "给出选择与一句理由。"
        )
        user = f"任务：{task}\n到场成员：{roster}"
        c = await structured_invoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)], RecipePlan
        )
        if c.mode not in _MODES:
            c.mode = "roundtable"
        return c

    return planner


def _drop_clarify(graph: dict) -> dict:
    """从图里去掉 clarify 节点并把 START 接到它的后继（保持合法）。"""
    g = deepcopy(graph)
    if not any(n["id"] == "clarify" for n in g["nodes"]):
        return g
    succ = next((e["to"] for e in g["edges"] if e["from"] == "clarify"), None)
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "clarify"]
    g["edges"] = [e for e in g["edges"] if e["from"] != "clarify" and e["to"] != "clarify"]
    if succ:
        g["edges"].insert(0, {"from": "START", "to": succ})
    return g


def assemble_recipe(plan: RecipePlan) -> dict:
    """据高层选择确定性拼图（保证合法）：选基模板 + 按 clarify 裁剪。"""
    if plan.mode == "fanout":
        base = FANOUT
    elif plan.human_in_loop:
        base = ROUNDTABLE
    else:
        base = ROUNDTABLE_CONTINUOUS
    graph = deepcopy(base)
    if not plan.clarify:
        graph = _drop_clarify(graph)
    return graph


def _name_for(task: str, plan: RecipePlan) -> str:
    head = (task or "").strip().replace("\n", " ")[:12]
    return f"AI·{_MODE_ZH.get(plan.mode, plan.mode)}：{head}" if head else f"AI·{_MODE_ZH.get(plan.mode, plan.mode)}"


async def plan_recipe(
    task: str, roster: list[str] | None = None, *, planner: RecipePlanner | None = None
) -> tuple[str, dict]:
    """L3：AI 产出 (name, graph)。planner=None → 默认圆桌（不调 LLM）；图非法→兜底圆桌。"""
    plan = await planner(task or "", roster or []) if planner else RecipePlan()
    graph = assemble_recipe(plan)
    if validate_recipe(graph):  # 兜底：assemble 应恒合法，万一不合法回退内置圆桌
        graph = deepcopy(ROUNDTABLE)
    return _name_for(task, plan), graph
