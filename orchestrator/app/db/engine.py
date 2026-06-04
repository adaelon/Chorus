"""S2.1: 异步 SQLite 引擎 / 建表 / 会话工厂（与 S2.0 的 aiosqlite 一致）。"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from . import models  # noqa: F401 - 导入以把表注册到 SQLModel.metadata


def make_engine(db_path: str = "chorus.sqlite") -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_sync_columns)


def _sync_columns(sync_conn) -> None:
    """轻量迁移：让**已存在**的表的列向模型对齐——补缺列（ADD）+ 删多余列（DROP）。

    无迁移框架（单机自包含、不引 alembic）：`create_all` 只建缺失整表、不改已存在表。故
    - 加字段（S7.1e llm_backends.kind/provider_id）→ "no such column"；
    - 改名/删字段（api_key_env→api_key）→ 残留旧列若 NOT NULL 无默认会 IntegrityError。
    这里比对模型列与库内列：差额列 `ADD COLUMN`（带标量默认）、多余列 `DROP COLUMN`（删列逐个
    try/except 保护，受限/不支持则跳过、不致命）。幂等、通用——"模型=唯一真相"，以后增删字段
    自动收敛，不用手动删库或写迁移。
    """
    inspector = sa_inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.tables.values():
        if table.name not in existing_tables:
            continue  # 全新表 create_all 已建齐
        have = {c["name"] for c in inspector.get_columns(table.name)}
        want = {c.name for c in table.columns}
        for col in table.columns:
            if col.name not in have:
                sync_conn.execute(text(_add_column_sql(table.name, col, sync_conn.dialect)))
        for orphan in have - want:  # 模型已删的旧列（如改名残留）：删掉，免 NOT NULL 残列挡 INSERT
            try:
                sync_conn.execute(text(f'ALTER TABLE "{table.name}" DROP COLUMN "{orphan}"'))
            except Exception:  # noqa: BLE001 - 受限列（PK/索引/旧 sqlite 不支持）跳过，不致命
                pass


def _add_column_sql(table_name: str, col, dialect) -> str:
    """造 `ALTER TABLE t ADD COLUMN c TYPE [DEFAULT v]`（仅标量默认；callable 默认如 created_at
    不会触发——那类列要么随整表新建、要么本就存在）。"""
    coltype = col.type.compile(dialect=dialect)
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {coltype}'
    default = col.default
    if default is not None and getattr(default, "is_scalar", False):
        val = default.arg
        if isinstance(val, bool):
            lit = "1" if val else "0"
        elif isinstance(val, (int, float)):
            lit = str(val)
        else:
            lit = "'" + str(val).replace("'", "''") + "'"
        sql += f" DEFAULT {lit}"
    return sql


def make_session_factory(engine: AsyncEngine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
