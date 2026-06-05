"""S11b: llm_plan streams chunks internally and checkpoints only closed intent.

P0 deliberately uses a narrow JSON intent protocol so tests can lock the
checkpoint semantics without depending on a specific provider's tool-calling
stream format.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..state import GroupState, SkillRef, ToolCallIntent, TraceEvent
from ._common import request_text

PlanStream = Callable[[GroupState], AsyncIterable[str]]


class _ToolIntentPayload(BaseModel):
    kind: Literal["tool_call"]
    call_id: str
    tool_kind: Literal["mcp_call", "sandbox_exec", "sandbox_skill"]
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    skill_refs: list[SkillRef] = Field(default_factory=list)
    requires_sandbox: bool = False
    sandbox_profile: str | None = None
    timeout_ms: int | None = None


class _FinalPayload(BaseModel):
    kind: Literal["final"]
    text: str


async def _collect_stream(stream: AsyncIterable[str]) -> str:
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(str(chunk))
    return "".join(chunks)


def _parse_payload(text: str) -> _ToolIntentPayload | _FinalPayload:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"llm_plan returned invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("llm_plan payload must be a JSON object")

    kind = raw.get("kind")
    try:
        if kind == "tool_call":
            return _ToolIntentPayload.model_validate(raw)
        if kind == "final":
            return _FinalPayload.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"llm_plan payload failed validation: {exc}") from exc
    raise ValueError(f"llm_plan payload has unsupported kind: {kind!r}")


def _intent_from_payload(payload: _ToolIntentPayload) -> ToolCallIntent:
    return ToolCallIntent(
        call_id=payload.call_id,
        kind=payload.tool_kind,
        tool_name=payload.tool_name,
        args=payload.args,
        skill_refs=payload.skill_refs,
        requires_sandbox=payload.requires_sandbox,
        sandbox_profile=payload.sandbox_profile,
        timeout_ms=payload.timeout_ms,
    )


async def llm_plan(
    state: GroupState,
    *,
    stream: PlanStream | None = None,
) -> dict:
    """Aggregate chunks into one closed assistant intent, then return state delta.

    Recovery contract:
    - existing pending tool or output means the intent has already closed, so do
      not call LLM again;
    - if streaming raises before returning, no delta is produced by this node.
    """
    if state.pending_tools or state.output:
        return {}
    if stream is None:
        raise RuntimeError("llm_plan requires an injected stream in S11b P0")

    text = await _collect_stream(stream(state))
    payload = _parse_payload(text)
    trace = list(state.trace_events)
    if isinstance(payload, _FinalPayload):
        trace.append(TraceEvent(node="llm_plan", status="closed", message="final"))
        return {"output": payload.text, "run_status": "done", "trace_events": trace}

    intent = _intent_from_payload(payload)
    trace.append(TraceEvent(node="llm_plan", status="closed", message="tool_call"))
    return {"pending_tools": [*state.pending_tools, intent], "trace_events": trace}


def prompt_for_plan(state: GroupState) -> str:
    """Small deterministic prompt helper for future real LLM wiring."""
    return request_text(state)
