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
