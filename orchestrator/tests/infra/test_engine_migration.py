"""S7.1e 修复判据：init_models 让老库列向模型对齐——补缺列 + 删多余列。

老库 llm_backends 缺 kind/provider_id（S7.1e 新增）→ no such column；改名残留 api_key_env
（NOT NULL 无默认）→ 新 INSERT 触 IntegrityError。轻量迁移应 ADD 缺列 + DROP 多余列。
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db.engine import init_models, make_engine, make_session_factory
from app.db.models import LLMBackend


async def test_init_models_syncs_columns(tmp_path):
    db = str(tmp_path / "old.sqlite")
    engine = make_engine(db)
    # 造"老库"：缺新列(kind/provider_id/api_key...) + 残留改名前的 api_key_env(NOT NULL 无默认)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE llm_backends "
                "(id VARCHAR PRIMARY KEY, name VARCHAR, api_key_env VARCHAR NOT NULL, created_at FLOAT)"
            )
        )
        await conn.execute(
            text("INSERT INTO llm_backends (id, name, api_key_env, created_at) VALUES ('old', '旧', 'OLD_KEY', 1.0)")
        )

    await init_models(engine)  # ADD 缺列 + DROP 多余列(api_key_env)

    async with engine.begin() as conn:
        cols = await conn.run_sync(lambda c: {col["name"] for col in sa_inspect(c).get_columns("llm_backends")})
    assert {"kind", "provider_id", "base_url", "api_key", "model", "temperature", "max_tokens"} <= cols
    assert "api_key_env" not in cols  # 多余旧列已删

    # 旧行可读 + ORM 增查不再 IntegrityError（NOT NULL 残列已除）
    sf = make_session_factory(engine)
    async with sf() as s:
        old = await s.get(LLMBackend, "old")
        assert old.kind == "openai" and old.provider_id == ""
        s.add(LLMBackend(id="new", name="新", kind="openai", base_url="https://x/v1", api_key="sk-x", model="m"))
        await s.commit()
        again = await s.get(LLMBackend, "new")
        assert again.api_key == "sk-x"
