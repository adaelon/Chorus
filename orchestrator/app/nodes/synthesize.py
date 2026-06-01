"""S1.6: SYNTHESIZE 节点——把策展结果汇成一份产出。

MVP 为简单拼接：有 picked 则汇总 picked，否则汇总现存候选。
（更聪明的"主笔"综合留后续切片。）
"""

from __future__ import annotations

from ..state import GroupState


async def synthesize(state: GroupState) -> dict:
    items = state.picked or state.candidates
    lines = [f"- [{c.contact_id}] {c.text}" for c in items]
    return {"output": "\n".join(lines)}
