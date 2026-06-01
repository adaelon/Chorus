"""S1.4: FRAME 节点——主持人读需求，给 roster 每人分一个临场维度。

用结构化输出让主持人 LLM 产出 contact_id→维度 的映射，合并回 roster。
`assign` 可注入以便测试不碰真实 LLM。人设注入仍在 S2.2。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..llm import make_chat_model, robust_ainvoke
from ..state import AgentSlot, GroupState
from ._common import request_text

DimensionMap = dict[str, str]  # contact_id -> dimension
AssignFn = Callable[[str, list[AgentSlot]], Awaitable[DimensionMap]]


class _DimAssignment(BaseModel):
    contact_id: str
    dimension: str


class _Dimensions(BaseModel):
    assignments: list[_DimAssignment]


def _extract_json_obj(text: str) -> dict:
    """从 LLM 文本里抽出最外层 JSON 对象（容忍 ```json 围栏 / 思考前言）。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"主持人未返回可解析的 JSON: {text!r}")
    return json.loads(match.group(0))


def _default_assigner(model: ChatOpenAI) -> AssignFn:
    # 后端(deepseek-v4-pro 思考模型)既不支持 response_format，也不支持强制 tool_choice，
    # 故不用 with_structured_output，改走纯文本 JSON + 自解析 + pydantic 校验。
    async def assign(request: str, roster: list[AgentSlot]) -> DimensionMap:
        ids = [s.contact_id for s in roster]
        system = (
            "你是圆桌主持人。给每个到场成员分配一个互不重叠、贴合需求的讨论维度"
            "（简短名词短语），每个成员恰好一个。"
            '只输出 JSON：{"assignments":[{"contact_id":"...","dimension":"..."}]}，'
            "不要任何额外文字。"
        )
        user = f"需求：{request}\n到场成员：{ids}"
        resp = await robust_ainvoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)]
        )
        dims = _Dimensions.model_validate(_extract_json_obj(str(resp.content)))
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
