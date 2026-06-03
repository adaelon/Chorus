"""S3.4: 人在环打断横切——圆桌每轮发言后给真人一个插话窗口。

复用 S3.0 的 interrupt 机制（§6.10）：节点 `interrupt()` 暂停 = 让位（AI 不抢麦，等人），
按 resume / 已注入的 `pending_human` 决定是否改向。三条通道统一在此消化：
  ① resume `{"interject": "..."}`：人当场插话；`{"interject": null}`：不插话、继续讨论
  ② 外部 `aupdate_state` 写 `pending_human`（service 异步注入通道）
  ③ resume `{"end": true}`：人手动收尾 → 去 SYNTHESIZE 主笔综合（S3.6h，不靠预算闸/主持人判停）
任一有人类输入 → 消息进 `history` + 预算闸 `turns_since_human` 归零（讨论改向）。

**S5.4.0b 路由出节点（§6.16 A.3）**：interrupt（暂停）留在节点，跳转（goto）抽到边——
本节点只写 state delta + `next_decision∈{continue,end}`，由配方的条件边路由
（continue→schedule / end→synthesize）。这样 human_gate 不再焊死拓扑，可被 L4 用户重新接线。

**注**：resume payload 必须是**非空**对象（LangGraph 把 falsy/空 resume 当作"未恢复"会重新
interrupt）；故"继续"用 `{"interject": null}` 而非 `{}`。
"""

from __future__ import annotations

from langgraph.types import interrupt

from ..state import GroupState, Msg


async def human_gate(state: GroupState) -> dict:
    """每轮后暂停等人：插话则纳入 history + 重置预算（改向），否则继续讨论。

    返回 delta 含 `next_decision`：`end`（人手动收尾→synthesize）/ `continue`（继续→schedule）。
    """
    history = list(state.history)
    had_pending = state.pending_human is not None
    if had_pending:
        history.append(state.pending_human)  # 异步注入的插话纳入群历史

    signal = interrupt(
        {
            "type": "human_gate",
            "turns_since_human": state.turns_since_human,
            "last": state.history[-1].text if state.history else None,
        }
    )

    if isinstance(signal, dict) and signal.get("end"):
        # 人手动收尾（S3.6h）：next_decision=end → 边路由去主笔综合，不再调度。
        return {"history": history, "pending_human": None, "next_decision": "end"}

    text = signal.get("interject") if isinstance(signal, dict) else None
    if text:
        history.append(Msg(sender_id="human", sender_kind="human", text=text))

    update: dict = {"history": history, "pending_human": None, "next_decision": "continue"}
    if had_pending or text:
        update["turns_since_human"] = 0  # 有人类输入 → 预算重置（改向）
    return update
