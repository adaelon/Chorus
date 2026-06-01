"""候选生成：给一个 agent + 需求，产一份候选。

FANOUT（并行）与 CURATE 的 reassign（定向）共用。
S2.2 起注入混合身份：基础人设 + 临场维度 + 群历史（§4）；无 persona 时回退占位。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..llm import robust_ainvoke
from ..state import AgentSlot, Candidate, Msg

# 生成单份候选：(slot, request, history) -> Candidate。可注入以便测试不碰真实 LLM。
GenerateFn = Callable[[AgentSlot, str, list[Msg]], Awaitable[Candidate]]
# 按 contact_id 取 persona（鸭子类型：需有 name/title/persona_style/base_stance）。
PersonaProvider = Callable[[str], Awaitable[object | None]]

_HISTORY_TAIL = 10


def placeholder_messages(slot: AgentSlot, request: str) -> list[BaseMessage]:
    """无 persona 时的占位 prompt（S1.3 行为）。"""
    system = f"You are participant {slot.contact_id}."
    user = f"{request}\n\n你负责的角度：{slot.dimension}" if slot.dimension else request
    return [SystemMessage(content=system), HumanMessage(content=user)]


def persona_messages(
    persona: object,
    dimension: str | None,
    history: list[Msg],
    request: str,
) -> list[BaseMessage]:
    """混合身份 prompt：基础人设 + 本场维度 + 群历史 + 当前需求（§4）。"""
    bio = f"你是{getattr(persona, 'name', '')}"
    if getattr(persona, "title", ""):
        bio += f"，{persona.title}"
    if getattr(persona, "persona_style", ""):
        bio += f"。说话风格：{persona.persona_style}"
    if getattr(persona, "base_stance", ""):
        bio += f"。底层立场：{persona.base_stance}"
    system = bio
    if dimension:
        system += f"\n本场你负责的维度：{dimension}"

    msgs: list[BaseMessage] = [SystemMessage(content=system)]
    recent = (history or [])[-_HISTORY_TAIL:]
    if recent:
        joined = "\n".join(f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in recent)
        msgs.append(SystemMessage(content=f"群历史：\n{joined}"))
    msgs.append(HumanMessage(content=request))
    return msgs


def default_generator(
    model: ChatOpenAI,
    persona_provider: PersonaProvider | None = None,
) -> GenerateFn:
    async def generate(slot: AgentSlot, request: str, history: list[Msg]) -> Candidate:
        persona = await persona_provider(slot.contact_id) if persona_provider else None
        msgs = (
            persona_messages(persona, slot.dimension, history, request)
            if persona is not None
            else placeholder_messages(slot, request)
        )
        resp = await robust_ainvoke(model, msgs)
        return Candidate(
            contact_id=slot.contact_id,
            dimension=slot.dimension,
            text=str(resp.content),
        )

    return generate
