"""S5.2: PLAN 节点——L3 主持人按任务**逐步组原语**（§6.13）。

把 SCHEDULE 的"只选下一个发言人"泛化成"选下一个原语动作"：

    PlanDecision = Fanout | Speak(contact_id) | Synthesize | Stop

引擎是单循环 `PLAN → dispatch(原语) → PLAN …`（见 recipes_auto）。"圆桌"="一直 Speak 轮转"、
"扇出"="一次 Fanout"都退化为该策略的特例。

**§B2 框定（安全）**：主持人 LLM 只"选哪个原语"（输入/建议）；原语执行 + **步数闸**
（`plan_steps >= max_plan_steps` 必 Stop）是确定性裁决，防 LLM 跑偏/死循环/烧钱。
`planner` 可注入离线测；AskHuman/Curate（人在环原语）框架可扩展，本刀先做自治四原语。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..llm import make_chat_model
from ..state import GroupState
from ..structured import structured_invoke


class Fanout(BaseModel):
    kind: Literal["fanout"] = "fanout"


class Speak(BaseModel):
    kind: Literal["speak"] = "speak"
    contact_id: str


class Synthesize(BaseModel):
    kind: Literal["synthesize"] = "synthesize"


class Stop(BaseModel):
    kind: Literal["stop"] = "stop"
    reason: str = ""


PlanDecision = Fanout | Speak | Synthesize | Stop

# (state) -> 下一个原语动作。默认 default_planner，可注入假实现离线测。
PlanFn = Callable[[GroupState], Awaitable[PlanDecision]]


class _PlanChoice(BaseModel):
    """主持人一次结构化选择：下一个原语动作。"""

    action: str  # fanout | speak | synthesize | stop
    contact_id: str | None = None  # action=speak 时，谁发言
    reason: str = ""


def default_planner(model: ChatOpenAI) -> PlanFn:
    """主持人 LLM 选下一个原语，复用 structured_invoke（§6.9）。"""

    async def plan_pick(state: GroupState) -> PlanDecision:
        ids = [s.contact_id for s in state.roster]
        points = "\n".join(f"- {c.speaker_id}：{c.text}" for c in state.claims) or "（暂无）"
        recent = "\n".join(
            f"{m.sender_id}（{m.sender_kind}）：{m.text}" for m in state.history[-6:]
        ) or "（暂无）"
        has_cand = "有" if state.candidates else "无"
        system = (
            "你是圆桌主持人，按任务进展选**下一个动作**，只能选其一：\n"
            "- fanout：让到场成员并行各出一版候选（适合开头发散/要多个方案）。\n"
            "- speak：让某个成员发一次言（推动讨论，需给 contact_id）。\n"
            "- synthesize：讨论已充分，主笔综合产出收尾。\n"
            "- stop：无法继续/已足够，直接停。\n"
            "speak 只能从给定成员里选。"
        )
        user = f"任务相关成员：{ids}\n当前候选：{has_cand}\n已有要点：\n{points}\n最近发言：\n{recent}"
        c = await structured_invoke(
            model, [SystemMessage(content=system), HumanMessage(content=user)], _PlanChoice
        )
        if c.action == "fanout":
            return Fanout()
        if c.action == "speak":
            cid = c.contact_id if c.contact_id in ids else (ids[0] if ids else "")
            return Speak(contact_id=cid) if cid else Stop(reason="empty_roster")
        if c.action == "synthesize":
            return Synthesize()
        return Stop(reason=c.reason or "planner")

    return plan_pick


async def plan(
    state: GroupState,
    *,
    planner: PlanFn | None = None,
    model: ChatOpenAI | None = None,
) -> dict:
    """PLAN 节点：步数闸优先 → 主持人选原语 → 落成 state delta（next_decision 供条件边路由）。

    步数闸（§B2 确定性裁决）：到 `max_plan_steps` 强制 synthesize 收尾，绝不无限循环。
    """
    if state.plan_steps >= state.max_plan_steps:
        return {"next_decision": "synthesize", "stop_reason": "plan_budget"}

    chooser = planner or default_planner(model or make_chat_model())
    decision = await chooser(state)
    delta: dict = {"plan_steps": state.plan_steps + 1}
    if isinstance(decision, Fanout):
        delta["next_decision"] = "fanout"
    elif isinstance(decision, Speak):
        delta["next_decision"] = "speak"
        delta["next_speaker"] = decision.contact_id
    elif isinstance(decision, Synthesize):
        delta["next_decision"] = "synthesize"
    else:  # Stop
        delta["next_decision"] = "synthesize"  # 停也走 synthesize 出产出再 END
        delta["stop_reason"] = decision.reason
    return delta
