"""S2.2: DB 支持的 persona_provider（按 contact_id 取 Contact）。

供 S2.4 wire 进 live 服务（create_app(persona_provider=...)）。
"""

from __future__ import annotations

from .models import Contact


def persona_provider_from(session_factory):
    """用会话工厂造一个 persona_provider：contact_id -> Contact | None。"""

    async def provider(contact_id: str):
        async with session_factory() as s:
            return await s.get(Contact, contact_id)

    return provider


def bot_ref_provider_from(session_factory):
    """用会话工厂造一个 bot_ref_provider：contact_id -> AstrBot platform 实例 id | None（S4.3）。

    出站时据此把"某 contact 发言"路由到对应 bot 实例；未绑定（空）则返回 None。
    """

    async def provider(contact_id: str) -> str | None:
        async with session_factory() as s:
            c = await s.get(Contact, contact_id)
            return c.bot_ref if (c and c.bot_ref) else None

    return provider


def reputation_adjuster_from(session_factory):
    """用会话工厂造一个 reputation_adjuster：(contact_id, delta) 软加权。

    只调整权重、绝不删除/处决 Contact（§8.4）；contact 不存在则忽略。
    """

    async def adjust(contact_id: str, delta: float) -> None:
        async with session_factory() as s:
            c = await s.get(Contact, contact_id)
            if c is not None:
                c.reputation += delta
                s.add(c)
                await s.commit()

    return adjust
