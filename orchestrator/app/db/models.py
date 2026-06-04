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
    llm_ref: str = ""  # LLMBackend id（推理选模型，S7.1b）；空=用全局默认 model
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


class Conversation(SQLModel, table=True):
    """会话索引（S5.7a，§6.17）：列出历史会话用。

    消息本体在 checkpointer 的 GroupState.history（single source）；这张表只补 checkpointer
    缺的"列出所有会话"能力 + 标题/配方。`id` = group_key（= thread_id）。`recipe_id` 空=默认圆桌。
    """

    __tablename__ = "conversations"

    id: str = Field(primary_key=True)
    title: str = ""
    recipe_id: str = ""  # "" = 默认 roundtable；否则库内配方 id（续跑取图用）
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class LLMBackend(SQLModel, table=True):
    """LLM 后端注册表（S7.1a，§6.18）：每好友独立模型的引用目标。

    `Contact.llm_ref` 指向本表 id；多好友可共享一个后端（同一 key 改一处）。
    **api_key 不落库**：只存 `api_key_env`（环境变量名，如 "DEEPSEEK_KEY"），真实 key 走环境变量，
    仓库可完整自包含、不泄密（延续 cmd_config 不进 git 的教训）。
    """

    __tablename__ = "llm_backends"

    id: str = Field(primary_key=True)
    name: str = ""
    kind: str = "openai"  # openai（独立自包含）| astrbot（委托 AstrBot provider），§6.18+ S7.1e
    base_url: str = ""
    api_key_env: str = ""  # 环境变量名（非明文 key）；kind=openai 构造 model 时从 os.environ 读
    model: str = ""
    temperature: float = 0.75
    max_tokens: int | None = None
    provider_id: str = ""  # kind=astrbot：委托的 AstrBot provider id（走桥 /llm）
    created_at: float = Field(default_factory=time.time)


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
