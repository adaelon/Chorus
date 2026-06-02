"""S3.1c: 中立提点原语——把一段发言压成 1-3 条带归属的"点"（§6.11）。

**中立提取**（非发言者自总结）：客观主张、无"王婆卖瓜"，低温小模型即可。
发言后调用（发言本身仍自然流式，SSE 不破）；走 `structured_invoke`（text_json 兜底，§6.9）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..state import Claim
from ..structured import structured_invoke

# 提点器：(发言文本, 发言人 contact_id, 轮次) -> 该发言的点列表。可注入以离线测试。
ClaimExtractor = Callable[[str, str, int], Awaitable[list[Claim]]]

MAX_POINTS = 3

_NEUTRAL_SYSTEM = (
    "你是中立的会议记录员。把下面这段发言压缩成 1-3 条最坚实的核心主张/立场，"
    "每条一句话、客观陈述、不加修辞、不评价、不替发言者表态。"
)


class _ExtractedPoints(BaseModel):
    points: list[str]  # 1-3 条一句话主张


def default_claim_extractor(model: ChatOpenAI) -> ClaimExtractor:
    """用 structured_invoke 中立提点的默认实现。"""

    async def extract(text: str, speaker_id: str, turn: int) -> list[Claim]:
        if not (text or "").strip():
            return []
        msgs = [SystemMessage(content=_NEUTRAL_SYSTEM), HumanMessage(content=f"发言：\n{text}")]
        res = await structured_invoke(model, msgs, _ExtractedPoints)
        return [
            Claim(speaker_id=speaker_id, text=p, turn=turn)
            for p in res.points[:MAX_POINTS]
        ]

    return extract
