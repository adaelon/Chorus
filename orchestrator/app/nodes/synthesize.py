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
from ._common import task_text


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


def default_produce_composer(model: ChatOpenAI) -> ComposeFn:
    """出产物主笔（§6.21）：把原始 task 当生产任务书、讨论要点当约束 → 交付产物本身。

    与 `default_composer`（出结论=收敛共识/分歧）相对：produce 不复述讨论，而是**执行任务**，
    直接给用户要的那个东西（prompt/方案/代码/文案…）。要点是输入与约束，不是结论。
    """

    async def compose(state: GroupState) -> str:
        task = task_text(state) or "（未给出明确诉求）"
        points = "\n".join(f"- {c.speaker_id}：{c.text}" for c in state.claims) or "（暂无要点）"
        recent = "\n".join(
            f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in state.history[-8:]
        )
        system = (
            "你是主笔。请基于圆桌讨论的要点，直接产出用户真正要的那个东西本身——"
            "他要 prompt 就写出一份可直接使用的 prompt，要方案就给方案，要文案/代码就给文案/代码。"
            "讨论要点是你的输入与约束，不是结论：不要复述讨论过程、不要写成“共识/分歧”会议纪要。"
            "先用一句话点明“我要交付的是：___”，紧接着给出产物本身。"
        )
        user = (
            f"用户最初的诉求：\n{task}\n\n"
            f"圆桌讨论要点（带归属，作为产物的约束/素材）：\n{points}\n\n"
            f"最近发言（原文）：\n{recent}"
        )
        resp = await robust_ainvoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)]
        )
        content = resp.content
        return content if isinstance(content, str) else str(content)

    return compose


async def produce(state: GroupState, *, compose_produce: ComposeFn | None = None) -> dict:
    """出产物原语（§6.21）：交付用户要的产物本身（非讨论纪要）。

    与 `synthesize`（出结论）同形（transform、写 output），区别只在主笔的脑子：用
    `compose_produce`（task 当生产任务书）。dep 键特意区别于 synthesize 的 `compose`，
    使编译器把"出产物主笔"灌进本节点、把"出结论主笔"灌进 synthesize。无 composer
    （离线/未配置）→ 退化为确定性兜底（claims 归并），不抛错。
    """
    if compose_produce is not None:
        return {"output": await compose_produce(state)}
    return {"output": _fallback_compose(state)}


async def synthesize(state: GroupState, *, compose: ComposeFn | None = None) -> dict:
    """统一终端节点：主笔综合 / 确定性汇候选 / 兜底归并（分流见模块 docstring）。"""
    if compose is not None:
        return {"output": await compose(state)}
    items = state.picked or state.candidates
    if items and not state.claims:  # 扇出：有候选且无 claims → 确定性汇候选
        return {"output": "\n".join(f"- [{c.contact_id}] {c.text}" for c in items)}
    return {"output": _fallback_compose(state)}  # 圆桌/auto 兜底：claims 归并/ai 史/空
