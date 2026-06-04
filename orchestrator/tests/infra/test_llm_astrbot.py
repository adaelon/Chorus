"""S7.1e 判据：kind=astrbot 后端——AstrBotChatModel 经桥委托 + make_model_from_backend 分流。

委托走 astream 接口（generate/turn 只认 astream），故能无缝插进 ModelProvider。`send` 注入
假桩离线测（不碰网络/桥）。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.db.models import LLMBackend
from app.llm import robust_ainvoke
from app.llm_astrbot import AstrBotChatModel, _bot_umo, _messages_to_payload, make_model_from_backend
from app.run_ctx import current_group_key


def test_messages_to_payload_maps_roles():
    msgs = [
        SystemMessage(content="你是A"),
        HumanMessage(content="前文"),
        AIMessage(content="回应"),
        HumanMessage(content="问题"),
    ]
    p = _messages_to_payload(msgs)
    assert p["system_prompt"] == "你是A"
    assert p["prompt"] == "问题"  # 末条 human 作 prompt
    assert p["contexts"] == [
        {"role": "user", "content": "前文"},
        {"role": "assistant", "content": "回应"},
    ]


async def test_astrbot_model_delegates_via_bridge():
    captured = {}

    async def fake_send(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {"ok": True, "text": "委托回包"}

    m = AstrBotChatModel("prov-1", "http://bridge:9876", send=fake_send)
    msg = await robust_ainvoke(m, [SystemMessage(content="你是A"), HumanMessage(content="问题")], attempts=1)
    assert msg.content == "委托回包"
    assert captured["url"] == "http://bridge:9876/llm"
    assert captured["payload"]["provider_id"] == "prov-1"
    assert captured["payload"]["prompt"] == "问题"


async def test_astrbot_model_error_raises():
    async def fake_send(url, payload):
        return {"ok": False, "error": "no provider"}

    m = AstrBotChatModel("p", "http://x", send=fake_send)
    with pytest.raises(RuntimeError):
        await robust_ainvoke(m, [HumanMessage(content="q")], attempts=1)


def test_astrbot_model_requires_provider_id_or_bot_ref():
    with pytest.raises(ValueError):
        AstrBotChatModel("", "http://x")  # 既无 provider_id 又无 bot_ref


def test_bot_umo_swaps_platform_segment():
    assert _bot_umo("botX", "telegram:GroupMessage:42") == "botX:GroupMessage:42"
    assert _bot_umo("botX", "weird") == "weird"  # 非三段兜底


async def test_astrbot_model_follow_bot_delegates_by_umo():
    """S7.3b：follow-bot 模式按 current_group_key 构造 bot-umo 委托。"""
    captured = {}

    async def fake_send(url, payload):
        captured["payload"] = payload
        return {"ok": True, "text": "跟随 bot 回包"}

    m = AstrBotChatModel(bridge_url="http://bridge:9876", bot_ref="botX", send=fake_send)
    token = current_group_key.set("telegram:GroupMessage:42")
    try:
        msg = await robust_ainvoke(m, [HumanMessage(content="问题")], attempts=1)
    finally:
        current_group_key.reset(token)
    assert msg.content == "跟随 bot 回包"
    assert captured["payload"]["umo"] == "botX:GroupMessage:42"
    assert "provider_id" not in captured["payload"]


async def test_astrbot_model_follow_bot_without_group_key_raises():
    m = AstrBotChatModel(bridge_url="http://x", bot_ref="botX", send=lambda u, p: None)
    current_group_key.set(None)
    with pytest.raises(RuntimeError):
        await robust_ainvoke(m, [HumanMessage(content="q")], attempts=1)


def test_make_model_from_backend_dispatch(monkeypatch):
    monkeypatch.setenv("LLM_STREAM_CHUNK_TIMEOUT", "0")
    oai = make_model_from_backend(
        LLMBackend(id="o", kind="openai", base_url="https://x/v1", api_key="sk-x", model="m"),
        bridge_url="http://x",
    )
    assert oai.__class__.__name__ == "ChatOpenAI"

    astr = make_model_from_backend(
        LLMBackend(id="a", kind="astrbot", provider_id="prov"), bridge_url="http://x"
    )
    assert isinstance(astr, AstrBotChatModel) and astr.provider_id == "prov"

    # kind 缺省按 openai（向后兼容）
    assert make_model_from_backend(
        LLMBackend(id="c", base_url="https://x/v1", api_key="sk-x", model="m"), bridge_url="http://x"
    ).__class__.__name__ == "ChatOpenAI"
