"""CLARIFY 节点——信心触发的轻澄清（§6.5 档位 B，S3.5 真实化）。

主持人先对需求做**信心自评**：
  信心 ≥ 阈值          → 直通进 FRAME（清晰需求不打扰）
  信心 < 阈值          → `interrupt` 回述理解 + 至多一问，等用户：
                           resume `{"answer": "..."}` → 答复并入 history，进 FRAME
                           resume `{"skip": true}`    → 跳过澄清，强制进 FRAME

复用 S3.0/S3.4 的 interrupt 机制（§6.10 模型 A）。`assess` 可注入；**为 None 时直通**
（保持 S1.4 占位行为，离线测试/未 wire 时零打扰）。阈值固定，不做自适应（Phase 2，§6.5）。

**注**：resume payload 必须非空（LangGraph 把 falsy resume 当"未恢复"会重触发 interrupt，
见 S3.4）；故跳过用 `{"skip": true}`、答复用 `{"answer": "..."}`。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel

from ..state import GroupState, Msg
from ..structured import structured_invoke
from ._common import request_text

CLARIFY_THRESHOLD = 0.6  # 信心阈值：克制（太高退化成重澄清，太低退化成零澄清，§6.5 命门）


class ClarifyAssessment(BaseModel):
    confidence: float  # 0..1：对需求清晰度/可执行性的信心
    restate: str = ""  # 回述理解（信心不足时给）
    question: str = ""  # 至多一问（信心不足时给）


# 信心自评：(request) -> 评估。可注入以离线测试；None 时 clarify 直通。
ClarifyFn = Callable[[str], Awaitable[ClarifyAssessment]]


def default_clarifier(model: ChatOpenAI) -> ClarifyFn:
    """主持人 LLM 信心自评，复用 structured_invoke（§6.9）。"""

    async def assess(request: str) -> ClarifyAssessment:
        system = (
            "你是圆桌主持人。评估这个需求是否足够清晰、可执行。给出 0~1 的信心分。"
            "若信心不足，用一句话回述你的理解，并提出**至多一个**最关键的澄清问题；"
            "信心充足则 question 留空。"
        )
        return await structured_invoke(
            model,
            [SystemMessage(content=system), HumanMessage(content=f"需求：{request}")],
            ClarifyAssessment,
        )

    return assess


async def clarify(
    state: GroupState,
    *,
    assess: ClarifyFn | None = None,
    threshold: float = CLARIFY_THRESHOLD,
) -> dict:
    """信心够则直通；不足则 interrupt 回述+一问，按 resume 跳过 / 并入答复。"""
    if assess is None:
        return {}  # 未配置评估器：直通（S1.4 占位行为）

    request = request_text(state)
    a = await assess(request)
    if a.confidence >= threshold:
        return {}  # 信心够，直通进 FRAME

    reply = interrupt({"type": "clarify", "restate": a.restate, "question": a.question})
    if isinstance(reply, dict) and reply.get("skip"):
        return {}  # 跳过：强制进 FRAME
    answer = reply.get("answer") if isinstance(reply, dict) else None
    if answer:
        msg = Msg(sender_id="human", sender_kind="human", text=answer)
        return {"history": [*state.history, msg]}  # 答复并入，进 FRAME
    return {}
