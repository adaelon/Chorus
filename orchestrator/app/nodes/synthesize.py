"""SYNTHESIZE 节点——把一场讨论汇成一份产出。

两个变体，分属两种配方：
  `synthesize`（扇出，S1.6）——汇 picked/candidates（人工策展过的候选）。
  `synthesize_roundtable`（圆桌，S3.6b）——圆桌无 candidates/picked，改从**点账本 claims**
    （远场带归属的主张）+ 近场 history 主笔综合。`compose` 可注入：默认 `default_composer`
    走 LLM 主笔；为 None 时确定性兜底（按 speaker 归并 claims），离线测试/未配置不打扰。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..llm import robust_ainvoke
from ..state import GroupState


async def synthesize(state: GroupState) -> dict:
    items = state.picked or state.candidates
    lines = [f"- [{c.contact_id}] {c.text}" for c in items]
    return {"output": "\n".join(lines)}


# 圆桌主笔：(state) -> 综合产出文本。可注入以离线测试；None 时走确定性兜底。
ComposeFn = Callable[[GroupState], Awaitable[str]]


def _fallback_compose(state: GroupState) -> str:
    """确定性兜底：按 speaker 归并点账本（无 LLM）。空账本退化为 ai 发言原文。"""
    by_speaker: dict[str, list[str]] = {}
    order: list[str] = []
    for c in state.claims:
        if c.speaker_id not in by_speaker:
            by_speaker[c.speaker_id] = []
            order.append(c.speaker_id)
        by_speaker[c.speaker_id].append(c.text)
    if order:
        return "\n".join(f"- [{sid}] " + "；".join(by_speaker[sid]) for sid in order)
    ai = [m for m in state.history if m.sender_kind == "ai"]
    return "\n".join(f"- [{m.sender_id}] {m.text}" for m in ai)


def default_composer(model: ChatOpenAI) -> ComposeFn:
    """圆桌主笔 LLM：读点账本 + 近场原文，综合成抓住共识与分歧的结论。"""

    async def compose(state: GroupState) -> str:
        points = "\n".join(f"- {c.speaker_id}：{c.text}" for c in state.claims) or "（暂无要点）"
        recent = "\n".join(
            f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in state.history[-8:]
        )
        system = (
            "你是圆桌主笔。基于各成员的要点与讨论原文，综合成一份连贯结论——"
            "既抓住共识，也点出关键分歧，不偏袒任一成员。"
        )
        user = f"讨论：\n{recent}\n\n各成员要点（带归属）：\n{points}"
        resp = await robust_ainvoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)]
        )
        content = resp.content
        return content if isinstance(content, str) else str(content)

    return compose


async def synthesize_roundtable(
    state: GroupState, *, compose: ComposeFn | None = None
) -> dict:
    """圆桌终端节点：主笔综合点账本/history。compose=None → 确定性兜底（不碰 LLM）。"""
    if compose is None:
        return {"output": _fallback_compose(state)}
    return {"output": await compose(state)}
