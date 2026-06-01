"""S1.3: FANOUT 节点——并行让 roster 里每个 agent 各产一份候选。

`asyncio.gather` 并行、互不可见（扇出模式，技术方案 §10.2/§10.3）。
本切片用占位 prompt，**不拼人设**（人设注入见 S2.2）；也不组装进图（见 S1.6）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..llm import make_chat_model, robust_ainvoke
from ..state import AgentSlot, Candidate, GroupState

# 生成单份候选的异步函数签名；可注入以便测试不碰真实 LLM。
GenerateFn = Callable[[AgentSlot, str], Awaitable[Candidate]]


def _request_text(state: GroupState) -> str:
    """取触发本轮扇出的人类需求：优先 pending_human，否则历史里最近一条人类消息。"""
    if state.pending_human is not None:
        return state.pending_human.text
    for msg in reversed(state.history):
        if msg.sender_kind == "human":
            return msg.text
    return ""


def _placeholder_messages(slot: AgentSlot, request: str) -> list[BaseMessage]:
    # 占位 prompt：不含人设（S2.2 再注入 base_persona + 群历史）
    system = f"You are participant {slot.contact_id}."
    user = f"{request}\n\n你负责的角度：{slot.dimension}" if slot.dimension else request
    return [SystemMessage(content=system), HumanMessage(content=user)]


def _default_generator(model: ChatOpenAI) -> GenerateFn:
    async def generate(slot: AgentSlot, request: str) -> Candidate:
        resp = await robust_ainvoke(model, _placeholder_messages(slot, request))
        return Candidate(
            contact_id=slot.contact_id,
            dimension=slot.dimension,
            text=str(resp.content),
        )

    return generate


async def fanout(
    state: GroupState,
    *,
    model: ChatOpenAI | None = None,
    generate: GenerateFn | None = None,
) -> dict:
    """并行生成 N 份候选，写回 state.candidates。

    LangGraph 节点：返回的 dict 会被合并进 state（candidates channel）。
    """
    gen = generate or _default_generator(model or make_chat_model())
    request = _request_text(state)
    candidates = await asyncio.gather(*(gen(slot, request) for slot in state.roster))
    return {"candidates": list(candidates)}
