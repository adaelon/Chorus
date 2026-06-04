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


class Claim(BaseModel):
    """点账本里的一条"点"：带归属的一句话主张（圆桌记忆压缩单位，§6.11）。"""

    speaker_id: str  # 谁主张的（归属——圆桌交锋的坐标）
    text: str  # 一句话的坚实主张
    turn: int = 0  # 第几轮产生（排序/衰减用）


class GroupState(BaseModel):
    """LangGraph 图的共享状态。配方(recipe)在其上演化。"""

    group_key: str
    roster: list[AgentSlot] = Field(default_factory=list)
    history: list[Msg] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    picked: list[Candidate] = Field(default_factory=list)  # 人工策展选中的点/候选
    claims: list[Claim] = Field(default_factory=list)  # 点账本（圆桌远场记忆，§6.11）
    output: str | None = None  # SYNTHESIZE 汇成的最终产出
    turns_since_human: int = 0
    max_turns_per_human: int = 6
    pending_human: Msg | None = None
    next_speaker: str | None = None  # 下一个发言的 contact_id（S3.2 SCHEDULE 决定，TURN 消费）
    next_decision: str | None = None  # 路由决策：圆桌 next_speaker|yield_to_human|stop；auto fanout|speak|synthesize|stop（S5.2）
    directed_queue: list[str] = Field(default_factory=list)  # §6.20 @定向插话：人点名的发言人队列（human_gate 填，schedule 按序 pop，跳过主持人/预算）
    directed_active: bool = False  # §6.20 当前 turn 是否定向修订（schedule 写、turn 读作"真人点名修改"框架）
    stop_reason: str | None = None  # Stop 的原因（budget|moderator|plan_budget…）
    plan_steps: int = 0  # L3 auto 配方：主持人已组的原语步数（S5.2）
    max_plan_steps: int = 8  # auto 配方步数闸（防跑偏/死循环；每步=plan+原语 2 超步，留 LangGraph 递归余量）
