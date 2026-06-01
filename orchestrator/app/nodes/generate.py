"""候选生成：给一个 agent + 需求，产一份候选。

FANOUT（并行）与 CURATE 的 reassign（定向）共用。本切片用占位 prompt，
不拼人设（人设注入见 S2.2）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..llm import robust_ainvoke
from ..state import AgentSlot, Candidate

# 生成单份候选的异步函数签名；可注入以便测试不碰真实 LLM。
GenerateFn = Callable[[AgentSlot, str], Awaitable[Candidate]]


def placeholder_messages(slot: AgentSlot, request: str) -> list[BaseMessage]:
    # 占位 prompt：不含人设（S2.2 再注入 base_persona + 群历史）
    system = f"You are participant {slot.contact_id}."
    user = f"{request}\n\n你负责的角度：{slot.dimension}" if slot.dimension else request
    return [SystemMessage(content=system), HumanMessage(content=user)]


def default_generator(model: ChatOpenAI) -> GenerateFn:
    async def generate(slot: AgentSlot, request: str) -> Candidate:
        resp = await robust_ainvoke(model, placeholder_messages(slot, request))
        return Candidate(
            contact_id=slot.contact_id,
            dimension=slot.dimension,
            text=str(resp.content),
        )

    return generate
