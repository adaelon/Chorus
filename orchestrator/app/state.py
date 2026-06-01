"""S1.1: 群聊编排的共享状态定义（GroupState）。

仅定义数据结构，不含任何节点逻辑 / LLM 调用（见切片计划 S1.1 的"不做"）。
对应技术方案 §3.2 GroupState、§5 数据模型。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentSlot(BaseModel):
    """一场讨论里的一个到场好友 + 其被分配的临场维度。"""

    contact_id: str
    dimension: str | None = None


class Msg(BaseModel):
    """群历史里的一条消息（短期记忆，MVP 只存本群）。"""

    sender_id: str
    sender_kind: str  # "human" | "ai" | "moderator"
    text: str
    dimension: str | None = None
    ts: float = 0.0


class Candidate(BaseModel):
    """扇出模式下，一个 agent 对当前需求产出的一份候选（技术方案 §10）。"""

    contact_id: str
    dimension: str | None = None
    text: str


class GroupState(BaseModel):
    """LangGraph 图的共享状态。配方(recipe)在其上演化。"""

    group_key: str
    roster: list[AgentSlot] = Field(default_factory=list)
    history: list[Msg] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    picked: list[Candidate] = Field(default_factory=list)  # 人工策展选中的点/候选
    output: str | None = None  # SYNTHESIZE 汇成的最终产出
    turns_since_human: int = 0
    max_turns_per_human: int = 6
    pending_human: Msg | None = None
