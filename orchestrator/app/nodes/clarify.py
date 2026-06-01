"""S1.4: CLARIFY 节点——本切片只是直通占位（恒"信心够"）。

真实的信心自评 + 回述/追问（技术方案 §6.5 档位 B）留到 S3.5。
这里不改 state，直接放行进 FRAME。
"""

from __future__ import annotations

from ..state import GroupState


async def clarify(state: GroupState) -> dict:
    # 占位：恒"信心够"，不产生澄清问题，直通。
    return {}
