"""S2.2: DB 支持的 persona_provider（按 contact_id 取 Contact）。

供 S2.4 wire 进 live 服务（create_app(persona_provider=...)）。
"""

from __future__ import annotations

import time

from sqlmodel import select

from ..llm_astrbot import make_model_from_backend
from ..recipes.builtin import (
    AUTO,
    FANOUT,
    ROUNDTABLE,
    ROUNDTABLE_CONTINUOUS,
    ROUNDTABLE_DELIVER,
    ROUNDTABLE_PRODUCE,
)
from .models import Contact, Conversation, LLMBackend, Recipe


async def _contact_bot_id(s, c: Contact) -> str:
    """好友的出站 bot id（S7.4b 统一）：优先 llm_ref→astrbot_bot 后端 .bot_id；legacy 回退 Contact.bot_ref。"""
    if c.llm_ref:
        b = await s.get(LLMBackend, c.llm_ref)
        if b is not None and b.kind == "astrbot_bot" and b.bot_id:
            return b.bot_id
    return c.bot_ref or ""  # 迁移兜底：老库好友仍直存 bot_ref

# 内置配方（S5.4.2a）：id = graph["recipe"] slug，启动 seed 进库、内置不可删。
_BUILTINS = (FANOUT, ROUNDTABLE, ROUNDTABLE_CONTINUOUS, ROUNDTABLE_PRODUCE, ROUNDTABLE_DELIVER, AUTO)


def persona_provider_from(session_factory):
    """用会话工厂造一个 persona_provider：contact_id -> Contact | None。"""

    async def provider(contact_id: str):
        async with session_factory() as s:
            return await s.get(Contact, contact_id)

    return provider


def roster_provider_from(session_factory):
    """造一个 roster_provider：() -> 能映射到出站 bot 的 Contact id 列表（= 群里 AI 参与者，S4.4）。

    S7.4b：判据从"有 bot_ref"改为"有出站 bot id"（llm_ref→astrbot_bot 后端 .bot_id，或 legacy bot_ref）。
    """

    async def provider() -> list[str]:
        async with session_factory() as s:
            contacts = (await s.exec(select(Contact))).all()
            return [c.id for c in contacts if await _contact_bot_id(s, c)]

    return provider


def model_provider_from(session_factory, *, cache: dict | None = None, bridge_url: str = "http://127.0.0.1:9876"):
    """造一个 model_provider：contact_id -> 模型 | None（S7.1b/e，§6.18 模型解耦）。

    按 `Contact.llm_ref → LLMBackend` 造该好友独立的模型（`make_model_from_backend` 按 kind 分流：
    openai→ChatOpenAI / astrbot→指定 provider 委托 / astrbot_bot→follow-bot 整 bot），**按 backend.id
    缓存**（不每轮新建，否则连接数爆炸）。无 llm_ref / 后端已删 → 返回 None（generate 回退全局默认
    model，现状不退化）。后端未填 api_key 时抛 MissingApiKey（清晰报错，不静默回退——用户明确绑了后端
    却不可用，应显式暴露）。缓存按 backend.id：CRUD 改后端配置后进程内仍旧值，重启生效（MVP 取舍）。
    """
    _cache: dict = {} if cache is None else cache

    async def provider(contact_id: str):
        async with session_factory() as s:
            c = await s.get(Contact, contact_id)
            if c is None or not c.llm_ref:
                return None
            b = await s.get(LLMBackend, c.llm_ref)
        if b is None:
            return None
        if b.id not in _cache:
            _cache[b.id] = make_model_from_backend(b, bridge_url=bridge_url)
        return _cache[b.id]

    return provider


def bot_ref_provider_from(session_factory):
    """用会话工厂造一个 bot_ref_provider：contact_id -> 出站 bot id | None（S4.3 / S7.4b 统一）。

    出站时据此把"某 contact 发言"路由到对应 bot 实例。S7.4b：优先 llm_ref→astrbot_bot 后端 .bot_id，
    legacy 回退 Contact.bot_ref；都没有则 None（未绑定）。
    """

    async def provider(contact_id: str) -> str | None:
        async with session_factory() as s:
            c = await s.get(Contact, contact_id)
            if c is None:
                return None
            return (await _contact_bot_id(s, c)) or None

    return provider


async def seed_builtin_recipes(session_factory) -> None:
    """启动幂等 seed 内置配方（S5.4.2a，含 S10a roundtable_produce）：缺则插、在则刷 graph/name。"""
    async with session_factory() as s:
        for g in _BUILTINS:
            rid = g["recipe"]
            obj = await s.get(Recipe, rid)
            if obj is None:
                s.add(Recipe(id=rid, name=rid, builtin=True, graph=g))
            else:
                obj.graph = g
                obj.builtin = True
                s.add(obj)
        await s.commit()


async def upsert_conversation(session_factory, group_key: str, title: str, recipe_id: str = "") -> None:
    """会话起场时登记/刷新索引（S5.7a）：新建记 created_at，已存在则 bump updated_at。"""
    async with session_factory() as s:
        obj = await s.get(Conversation, group_key)
        now = time.time()
        if obj is None:
            s.add(Conversation(id=group_key, title=title[:200], recipe_id=recipe_id, created_at=now, updated_at=now))
        else:
            obj.updated_at = now
            s.add(obj)
        await s.commit()


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
