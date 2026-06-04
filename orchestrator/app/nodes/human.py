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

    # §6.19：本次暂停的原因（主持人建议结束 moderator / 预算闸触顶 budget / 普通每轮 None）。
    # 主持人 stop 与预算闸经配方边路由到这里（而非直接收尾），由人拍板。
    reason = state.stop_reason

    signal = interrupt(
        {
            "type": "human_gate",
            "reason": reason,  # 供前端显示"为何被问"（S8b）
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

    # §6.20 @定向插话：resume 带 `directed`（前端 chips 选 → contact_id 列表，避重名/解析坑）
    # → 填 directed_queue，schedule 按序只让这几位修改（指令文本已作上面的 human 消息进 history）。
    directed = signal.get("directed") if isinstance(signal, dict) else None

    # 清掉 stop_reason，避免回 schedule 后旧 reason 再触发同一条边。
    update: dict = {"history": history, "pending_human": None, "next_decision": "continue", "stop_reason": None}
    if directed:
        update["directed_queue"] = [str(c) for c in directed]
    # 有人类输入，或本次是预算闸触顶的让位 → 预算归零（否则回 schedule 立刻再触顶，死循环）。
    if had_pending or text or reason == "budget":
        update["turns_since_human"] = 0
    return update


async def deliver(state: GroupState) -> dict:
    """出产物形态选择闸（§6.21/S10b）：结束讨论时问人"要结论还是产出"，只写路由、不产出。

    human 原语（interrupt 暂停等人）但**纯选择闸**——恢复后据人选写 `next_decision∈{decide,produce}`，
    由配方条件边路由到 `synthesize`（出结论）/ `produce`（出产物），自己不碰 output（复用两主笔）。
    用在"结束才知道要哪种"的 `roundtable_deliver`：human_gate 的 end 不直奔 synthesize，而是先过这里。
    resume 必须**非空**（同 human_gate）：用 `{"choice": "produce"|"decide"}`，缺省/非 produce → decide。
    """
    signal = interrupt({"type": "deliver", "options": ["decide", "produce"]})
    choice = signal.get("choice") if isinstance(signal, dict) else None
    return {"next_decision": "produce" if choice == "produce" else "decide"}
