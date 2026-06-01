"""S1.4: FRAME 节点——主持人读需求，给 roster 每人分一个临场维度。

用结构化输出让主持人 LLM 产出 contact_id→维度 的映射，合并回 roster。
`assign` 可注入以便测试不碰真实 LLM。人设注入仍在 S2.2。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..llm import make_chat_model
from ..state import AgentSlot, GroupState
from ..structured import structured_invoke
from ._common import request_text

DimensionMap = dict[str, str]  # contact_id -> dimension
AssignFn = Callable[[str, list[AgentSlot]], Awaitable[DimensionMap]]


class _DimAssignment(BaseModel):
    contact_id: str
    dimension: str


class _Dimensions(BaseModel):
    assignments: list[_DimAssignment]


def _default_assigner(model: ChatOpenAI) -> AssignFn:
    # 结构化输出走 structured_invoke（策略由配置决定，当前后端=text_json，见 §6.9）。
    async def assign(request: str, roster: list[AgentSlot]) -> DimensionMap:
        ids = [s.contact_id for s in roster]
        system = (
            "你是圆桌主持人。给每个到场成员分配一个互不重叠、贴合需求的讨论维度"
            "（简短名词短语），每个成员恰好一个。"
        )
        user = f"需求：{request}\n到场成员：{ids}"
        dims = await structured_invoke(
            model,
            [SystemMessage(content=system), HumanMessage(content=user)],
            _Dimensions,
        )
        return {a.contact_id: a.dimension for a in dims.assignments}

    return assign


async def frame(
    state: GroupState,
    *,
    model: ChatOpenAI | None = None,
    assign: AssignFn | None = None,
) -> dict:
    """给 roster 每人分配 dimension，返回更新后的 roster。"""
    fn = assign or _default_assigner(model or make_chat_model())
    request = request_text(state)
    mapping = await fn(request, state.roster)
    new_roster = [
        slot.model_copy(
            update={"dimension": mapping.get(slot.contact_id, slot.dimension)}
        )
        for slot in state.roster
    ]
    return {"roster": new_roster}
