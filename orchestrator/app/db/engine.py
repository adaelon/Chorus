"""S2.1: 异步 SQLite 引擎 / 建表 / 会话工厂（与 S2.0 的 aiosqlite 一致）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from . import models  # noqa: F401 - 导入以把表注册到 SQLModel.metadata


def make_engine(db_path: str = "chorus.sqlite") -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


def make_session_factory(engine: AsyncEngine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
