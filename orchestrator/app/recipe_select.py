"""S5.1: L2 主持人荐配方——按任务在**已测静态配方库**里选（§6.13）。

L1 是用户自己选（前端 RecipePicker）；L2 是主持人一次廉价 LLM 调用替用户选：
  roundtable —— 多人轮流讨论/辩论（决策、评估、"要不要做 X"）
  fanout     —— 多人并行各出方案 + 人工策展（创作、写稿、出点子、要多个候选）
选不准/非法 → 兜底默认 roundtable（不赌错成奇怪流程）。`selector` 可注入离线测；
为 None 时直接返回默认（不调 LLM）。L3（主持人逐步组原语）= S5.2。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .structured import structured_invoke

RECIPES = ("roundtable", "fanout")
DEFAULT_RECIPE = "roundtable"


class RecipeChoice(BaseModel):
    recipe: str  # roundtable | fanout
    reason: str = ""


# (task) -> RecipeChoice。可注入离线测；None 时 select_recipe 返回默认（不调 LLM）。
RecipeSelector = Callable[[str], Awaitable[RecipeChoice]]


def default_recipe_selector(model: ChatOpenAI) -> RecipeSelector:
    """主持人 LLM 选配方，复用 structured_invoke（§6.9）。"""

    async def select(task: str) -> RecipeChoice:
        system = (
            "你是圆桌主持人，按用户任务选最合适的协作配方，只能选 roundtable 或 fanout：\n"
            "- roundtable：多人轮流讨论/辩论，适合决策、评估、'要不要做X'、找共识与分歧。\n"
            "- fanout：多人并行各出一版方案再由人策展，适合创作、写稿、起名、要多个候选挑。\n"
            "给出 recipe 和一句 reason。"
        )
        choice = await structured_invoke(
            model, [SystemMessage(content=system), HumanMessage(content=f"任务：{task}")], RecipeChoice
        )
        if choice.recipe not in RECIPES:
            return RecipeChoice(recipe=DEFAULT_RECIPE, reason="未识别配方，兜底默认")
        return choice

    return select


async def select_recipe(task: str, *, selector: RecipeSelector | None = None) -> RecipeChoice:
    """选配方；未配置 selector → 默认 roundtable（不调 LLM）。空任务也兜底默认。"""
    if selector is None or not (task or "").strip():
        return RecipeChoice(recipe=DEFAULT_RECIPE, reason="默认（未配置荐配方或空任务）")
    return await selector(task)
