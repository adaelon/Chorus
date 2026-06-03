"""S2.1: 持久层数据模型（Contact / Group / Message），技术方案 §5。

与 `app/state.py` 的运行态模型（GroupState/Candidate/Msg）区分：这里是落库的表。
信誉字段留 S2.3；人设注入(S2.2)/UI(S2.4) 另行。
表名显式避开 SQL 保留字（groups）。
"""

from __future__ import annotations

import time

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Contact(SQLModel, table=True):
    """好友注册表：持久身份。"""

    __tablename__ = "contacts"

    id: str = Field(primary_key=True)
    name: str
    title: str = ""
    persona_style: str = ""
    base_stance: str = ""
    bot_ref: str = ""  # AstrBot platform 实例 id（出站选 bot），S4 绑定
    reputation: float = 0.0  # 软加权(§8.4)：人工 pick/eliminate 调整，非处决、可逆
    created_at: float = Field(default_factory=time.time)


class Group(SQLModel, table=True):
    """群：成员 / 主题 / 状态。"""

    __tablename__ = "groups"

    id: str = Field(primary_key=True)
    platform: str = ""
    group_key: str = Field(index=True)
    topic: str | None = None
    state: str = "idle"  # idle | discussing
    member_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class Message(SQLModel, table=True):
    """群历史（短期记忆，MVP 只本群）。"""

    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    group_key: str = Field(index=True)
    sender_id: str
    sender_kind: str  # human | ai | moderator
    text: str
    dimension: str | None = None
    ts: float = Field(default_factory=time.time)


class Recipe(SQLModel, table=True):
    """配方库（S5.4.2a，§6.16）：用户/内置的声明式 DAG（nodes/edges）。

    `graph` 是图原生 JSON（compile_recipe/validate_recipe 的输入）；`builtin` 标记内置三/四配方，
    内置不可删（启动 seed，见 repo.seed_builtin_recipes）。
    """

    __tablename__ = "recipes"

    id: str = Field(primary_key=True)
    name: str = ""
    builtin: bool = False
    graph: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
