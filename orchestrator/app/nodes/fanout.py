"""S1.3: FANOUT 节点——并行让 roster 里每个 agent 各产一份候选。

`asyncio.gather` 并行、互不可见（扇出模式，技术方案 §10.2/§10.3）。
候选生成逻辑共用 `generate.py`（FANOUT 与 CURATE.reassign 同源）。
"""

from __future__ import annotations

import asyncio

from langchain_openai import ChatOpenAI

from ..llm import make_chat_model
from ..state import GroupState
from ._common import request_text
from .generate import GenerateFn, ModelProvider, PersonaProvider, default_generator


async def fanout(
    state: GroupState,
    *,
    model: ChatOpenAI | None = None,
    generate: GenerateFn | None = None,
    persona_provider: PersonaProvider | None = None,
    model_provider: ModelProvider | None = None,
) -> dict:
    """并行生成 N 份候选，写回 state.candidates。

    LangGraph 节点：返回的 dict 会被合并进 state（candidates channel）。
    """
    gen = generate or default_generator(
        model or make_chat_model(), persona_provider, model_provider=model_provider
    )
    request = request_text(state)
    candidates = await asyncio.gather(
        *(gen(slot, request, state.history, state.claims) for slot in state.roster)
    )
    return {"candidates": list(candidates)}
