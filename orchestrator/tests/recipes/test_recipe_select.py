"""S5.1 判据：L2 荐配方——讨论型→roundtable、创作型→fanout、非法/无 selector→兜底默认。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.messages import AIMessageChunk

from app.recipes.select import RecipeChoice, default_recipe_selector, select_recipe
from app.service import create_app


class _FakeModel:
    """假模型：astream 恒返回预设 JSON（测 default_recipe_selector 的兜底分支）。"""

    def __init__(self, content: str):
        self.content = content

    async def astream(self, messages, config=None):  # noqa: ANN001
        yield AIMessageChunk(content=self.content)


def _by_keyword():
    """含'写/创作/起名'→fanout，否则 roundtable（模拟主持人判断）。"""

    async def selector(task: str) -> RecipeChoice:
        if any(k in task for k in ("写", "创作", "起名", "文案")):
            return RecipeChoice(recipe="fanout", reason="创作型")
        return RecipeChoice(recipe="roundtable", reason="讨论型")

    return selector


async def test_discussion_task_picks_roundtable():
    c = await select_recipe("要不要给便利店做付费会员", selector=_by_keyword())
    assert c.recipe == "roundtable"


async def test_creation_task_picks_fanout():
    c = await select_recipe("帮我写一条春节促销文案", selector=_by_keyword())
    assert c.recipe == "fanout"


async def test_none_selector_and_empty_task_default():
    assert (await select_recipe("任何任务", selector=None)).recipe == "roundtable"
    assert (await select_recipe("   ", selector=_by_keyword())).recipe == "roundtable"


async def test_default_selector_falls_back_on_invalid_recipe():
    """default_recipe_selector：LLM 返回非法配方名 → 兜底默认 roundtable。"""
    sel = default_recipe_selector(_FakeModel('{"recipe":"nonsense","reason":"x"}'))
    assert (await sel("某任务")).recipe == "roundtable"


async def test_default_selector_honors_valid_recipe():
    sel = default_recipe_selector(_FakeModel('{"recipe":"fanout","reason":"创作"}'))
    assert (await sel("写文案")).recipe == "fanout"


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        recipe_selector=_by_keyword(),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_recipe_select_endpoint(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/recipe/select", json={"task": "要不要做付费会员"})
        assert r.status_code == 200 and r.json()["recipe"] == "roundtable"
        r2 = client.post("/recipe/select", json={"task": "帮我写文案"})
        assert r2.json()["recipe"] == "fanout"
