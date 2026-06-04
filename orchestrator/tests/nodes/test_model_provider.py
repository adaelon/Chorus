"""S7.1b 判据：每好友独立 LLM 后端（§6.18 模型解耦）。

- `default_generator` 按 `model_provider(contact_id)` 选模型；无绑定→回退全局（现状不退化）。
- `model_provider_from`：按 `Contact.llm_ref→LLMBackend` 造模型、**按 backend 缓存**、无绑定→None。
"""

from __future__ import annotations

import pytest

from app.db.engine import init_models, make_engine, make_session_factory
from app.db.models import Contact, LLMBackend
from app.db.repo import model_provider_from
from app.nodes.generate import default_generator
from app.state import AgentSlot


class _FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    """最小流式模型：astream 吐一个标记内容，证明用的是哪个模型。"""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def astream(self, messages, config=None):
        async def gen():
            yield _FakeChunk(f"[{self.tag}]")

        return gen()


async def test_generate_routes_per_contact_model():
    a, b, glob = _FakeModel("A"), _FakeModel("B"), _FakeModel("GLOBAL")

    async def mp(contact_id: str):
        return {"ada1": a, "ada2": b}.get(contact_id)  # ada3 → None（回退）

    gen = default_generator(glob, model_provider=mp)
    assert (await gen(AgentSlot(contact_id="ada1"), "q", [])).text == "[A]"
    assert (await gen(AgentSlot(contact_id="ada2"), "q", [])).text == "[B]"
    # 无绑定 → 回退全局 model（不退化）
    assert (await gen(AgentSlot(contact_id="ada3"), "q", [])).text == "[GLOBAL]"


async def _sf(tmp_path):
    engine = make_engine(str(tmp_path / "reg.sqlite"))
    await init_models(engine)
    return make_session_factory(engine)


async def test_model_provider_from_binds_caches_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_STREAM_CHUNK_TIMEOUT", "0")  # 禁看门狗，免依赖完整 LLM_* 环境
    sf = await _sf(tmp_path)
    async with sf() as s:
        s.add(LLMBackend(id="gpt", name="GPT", base_url="https://o/v1", api_key="sk-gpt", model="gpt-4o"))
        s.add(LLMBackend(id="ds", name="DeepSeek", base_url="https://d/v1", api_key="sk-ds", model="deepseek-chat"))
        s.add(Contact(id="ada1", name="阿达1", llm_ref="gpt"))
        s.add(Contact(id="ada2", name="阿达2", llm_ref="ds"))
        s.add(Contact(id="ada3", name="阿达3"))  # 无 llm_ref
        await s.commit()

    provider = model_provider_from(sf)
    m1 = await provider("ada1")
    m2 = await provider("ada2")
    assert m1.model_name == "gpt-4o" and m2.model_name == "deepseek-chat"  # 各绑各的
    assert await provider("ada3") is None  # 无绑定 → 回退
    assert await provider("zzz") is None  # 好友不存在 → 回退
    # 缓存：同 backend 第二次取返回同一实例（不重建连接）
    assert await provider("ada1") is m1


async def test_model_provider_from_unbound_backend_falls_back(tmp_path):
    sf = await _sf(tmp_path)
    async with sf() as s:
        s.add(Contact(id="ada", name="阿达", llm_ref="ghost"))  # 指向已删/不存在的后端
        await s.commit()
    assert await model_provider_from(sf)("ada") is None


async def test_model_provider_from_astrbot_kind(tmp_path):
    """S7.1e：好友绑 kind=astrbot 后端 → provider 返回 AstrBotChatModel（委托桥）。"""
    from app.llm_astrbot import AstrBotChatModel

    sf = await _sf(tmp_path)
    async with sf() as s:
        s.add(LLMBackend(id="ab", name="经 AstrBot", kind="astrbot", provider_id="prov-x"))
        s.add(Contact(id="ada", name="阿达", llm_ref="ab"))
        await s.commit()
    m = await model_provider_from(sf, bridge_url="http://bridge:9876")("ada")
    assert isinstance(m, AstrBotChatModel) and m.provider_id == "prov-x"
