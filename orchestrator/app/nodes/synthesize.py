"""SYNTHESIZE 节点——把一场讨论汇成一份产出（S5.4.0e 两变体合一）。

一个统一节点 `synthesize(state, *, compose=None)`，按状态分流（§6.16 A.2）：
  1) 有主笔 `compose`（圆桌/auto/relay 注入）→ LLM 主笔综合点账本 + 近场 history。
  2) 有候选/选中且**无 claims**（扇出：无人发言成点）→ 确定性汇 picked/candidates。
  3) 其余（圆桌 compose=None）→ 确定性兜底 `_fallback_compose`（按 speaker 归并 claims，
     空账本退化为 ai 发言原文，再空则空串）。
分流读 state（claims/picked/candidates）+ 注入的 compose，故扇出/圆桌/auto 各得其所，
加配方不必再各配一个 SYNTHESIZE 变体。`compose` 默认 `default_composer`（LLM），离线测试/
未配置走兜底不打扰。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..llm import robust_ainvoke
from ..state import GroupState


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


async def synthesize(state: GroupState, *, compose: ComposeFn | None = None) -> dict:
    """统一终端节点：主笔综合 / 确定性汇候选 / 兜底归并（分流见模块 docstring）。"""
    if compose is not None:
        return {"output": await compose(state)}
    items = state.picked or state.candidates
    if items and not state.claims:  # 扇出：有候选且无 claims → 确定性汇候选
        return {"output": "\n".join(f"- [{c.contact_id}] {c.text}" for c in items)}
    return {"output": _fallback_compose(state)}  # 圆桌/auto 兜底：claims 归并/ai 史/空
