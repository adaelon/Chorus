"""候选生成：给一个 agent + 需求，产一份候选。

FANOUT（并行）与 CURATE 的 reassign（定向）共用。
S2.2 起注入混合身份：基础人设 + 临场维度 + 群历史（§4）；无 persona 时回退占位。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..context import ContextProjector, default_context_projector
from ..llm import robust_ainvoke
from ..state import AgentSlot, Candidate, Claim, Msg

# 生成单份候选：(slot, request, history, claims) -> Candidate。可注入以便测试不碰真实 LLM。
# claims 为可选第 4 参（圆桌点账本，§6.11）；扇出场景常为空。
GenerateFn = Callable[..., Awaitable[Candidate]]
# 按 contact_id 取 persona（鸭子类型：需有 name/title/persona_style/base_stance）。
PersonaProvider = Callable[[str], Awaitable[object | None]]
# 按 contact_id 取该好友独立的 LLM（S7.1b，§6.18）；返回 None=回退全局默认 model。
ModelProvider = Callable[[str], Awaitable[ChatOpenAI | None]]


def placeholder_messages(
    slot: AgentSlot,
    request: str,
    history: list[Msg] | None = None,
    claims: list[Claim] | None = None,
    *,
    projector: ContextProjector = default_context_projector,
) -> list[BaseMessage]:
    """无 persona 时的占位 prompt。S3.1b 起也经投影器看上下文（不再"失忆"）。"""
    system = f"You are participant {slot.contact_id}."
    if slot.dimension:
        system += f"\n你负责的角度：{slot.dimension}"
    msgs: list[BaseMessage] = [SystemMessage(content=system)]
    msgs += projector(history or [], claims or [])
    msgs.append(HumanMessage(content=request))
    return msgs


def persona_messages(
    persona: object,
    dimension: str | None,
    history: list[Msg],
    request: str,
    claims: list[Claim] | None = None,
    *,
    projector: ContextProjector = default_context_projector,
) -> list[BaseMessage]:
    """混合身份 prompt：基础人设 + 本场维度 + 投影上下文(远场点+近场原文) + 当前需求（§4/§6.11）。"""
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
    msgs += projector(history or [], claims or [])
    msgs.append(HumanMessage(content=request))
    return msgs


def default_generator(
    model: ChatOpenAI,
    persona_provider: PersonaProvider | None = None,
    *,
    model_provider: ModelProvider | None = None,
    projector: ContextProjector = default_context_projector,
) -> GenerateFn:
    """造一个 generate：按 persona_provider 注入人设、按 model_provider 选每好友模型。

    `model_provider`（S7.1b，§6.18）按 contact_id 取该好友绑定的模型；返回 None 或未注入时
    回退入参 `model`（全局默认）。这样 ada1 可用 gpt、ada2 可用 deepseek，同场各说各的模型。
    """
    async def generate(
        slot: AgentSlot,
        request: str,
        history: list[Msg],
        claims: list[Claim] | None = None,
    ) -> Candidate:
        persona = await persona_provider(slot.contact_id) if persona_provider else None
        msgs = (
            persona_messages(persona, slot.dimension, history, request, claims, projector=projector)
            if persona is not None
            else placeholder_messages(slot, request, history, claims, projector=projector)
        )
        m = (await model_provider(slot.contact_id)) if model_provider else None
        m = m or model  # 无绑定/未注入 → 全局默认（现状不退化）
        # 打 contact_id tag/metadata，供 LangGraph messages 流把 token 路由到对应候选卡。
        config = {
            "tags": [f"agent:{slot.contact_id}"],
            "metadata": {"contact_id": slot.contact_id},
        }
        resp = await robust_ainvoke(m, msgs, config=config)
        return Candidate(
            contact_id=slot.contact_id,
            dimension=slot.dimension,
            text=str(resp.content),
        )

    return generate
