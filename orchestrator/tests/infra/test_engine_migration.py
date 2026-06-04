"""S7.1e 修复判据：init_models 给老库补缺列（ADD COLUMN），不再 no such column。

老库的 llm_backends 缺 kind/provider_id（S7.1e 新增）→ create_all 不改已存在表 → 查询报错。
轻量迁移应在 init 时把缺列补齐、可正常增删查。
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db.engine import init_models, make_engine, make_session_factory
from app.db.models import LLMBackend


async def test_init_models_adds_missing_columns(tmp_path):
    db = str(tmp_path / "old.sqlite")
    engine = make_engine(db)
    # 造"老库"：手建只有旧三列的 llm_backends（模拟 S7.1a 时的表）
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE llm_backends (id VARCHAR PRIMARY KEY, name VARCHAR, created_at FLOAT)")
        )
        await conn.execute(text("INSERT INTO llm_backends (id, name, created_at) VALUES ('old', '旧后端', 1.0)"))

    await init_models(engine)  # 应补齐 kind/provider_id/base_url/...

    async with engine.begin() as conn:
        cols = await conn.run_sync(lambda c: {col["name"] for col in sa_inspect(c).get_columns("llm_backends")})
    assert {"kind", "provider_id", "base_url", "api_key_env", "model", "temperature", "max_tokens"} <= cols

    # 补列带默认值：旧行可正常读，且能用 ORM 增查（不再 OperationalError）
    sf = make_session_factory(engine)
    async with sf() as s:
        old = await s.get(LLMBackend, "old")
        assert old.kind == "openai" and old.provider_id == ""
        s.add(LLMBackend(id="new", name="新", kind="astrbot", provider_id="prov"))
        await s.commit()
        again = await s.get(LLMBackend, "new")
        assert again.kind == "astrbot"
