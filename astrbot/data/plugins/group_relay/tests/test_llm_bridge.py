"""S7.1e/3a 判据（离线纯逻辑）：/llm 委托——按 provider_id 或 umo 取 provider、text_chat、回文本。

只测 llm_bridge.py（无 astrbot 依赖）：假 get_provider_by_id / get_using_provider + 假 provider 桩。
真实"Chorus kind=astrbot/跟随 bot → 桥 → AstrBot provider 发言"在 AstrBot 进程里手动验。
运行： orchestrator/.venv/Scripts/python -m pytest astrbot/data/plugins/group_relay/tests
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 插件目录，导入 llm_bridge

from llm_bridge import do_llm  # noqa: E402


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class _FakeProvider:
    def __init__(self) -> None:
        self.calls = []

    async def text_chat(self, prompt=None, contexts=None, system_prompt=None, **kw):
        self.calls.append({"prompt": prompt, "contexts": contexts, "system_prompt": system_prompt})
        return _FakeResp(f"[{system_prompt}] {prompt}")


def _by_id(insts: dict):
    return lambda pid: insts.get(pid)


def _by_umo(insts: dict):
    return lambda umo: insts.get(umo)


def test_llm_delegates_by_provider_id():
    """S7.1e：显式 provider_id。"""
    prov = _FakeProvider()
    payload = {
        "provider_id": "p1",
        "system_prompt": "你是A",
        "contexts": [{"role": "user", "content": "前文"}],
        "prompt": "问题",
    }
    body, status = asyncio.run(do_llm(_by_id({"p1": prov}), _by_umo({}), payload))
    assert status == 200 and body["ok"] is True
    assert body["text"] == "[你是A] 问题" and body["provider_id"] == "p1"
    assert prov.calls == [{"prompt": "问题", "contexts": [{"role": "user", "content": "前文"}], "system_prompt": "你是A"}]


def test_llm_delegates_by_umo():
    """S7.3a：按 umo 取该 bot 在用的 provider（整 bot 引用 C）。"""
    prov = _FakeProvider()
    payload = {"umo": "telegram:GroupMessage:42", "system_prompt": "你是B", "prompt": "问题"}
    body, status = asyncio.run(do_llm(_by_id({}), _by_umo({"telegram:GroupMessage:42": prov}), payload))
    assert status == 200 and body["ok"] is True
    assert body["text"] == "[你是B] 问题" and body["umo"] == "telegram:GroupMessage:42"


def test_llm_unknown_provider_returns_404():
    # provider_id 找不到
    body, status = asyncio.run(do_llm(_by_id({}), _by_umo({}), {"provider_id": "ghost", "prompt": "q"}))
    assert status == 404 and body["ok"] is False
    # umo 取不到 using-provider
    body, status = asyncio.run(do_llm(_by_id({}), _by_umo({}), {"umo": "x:y:z", "prompt": "q"}))
    assert status == 404 and body["ok"] is False


def test_llm_bad_payload_returns_400():
    # 缺 prompt / 既无 provider_id 又无 umo / 非 dict
    for payload in [{}, {"provider_id": "p"}, {"umo": "u"}, {"prompt": "q"}, "notdict"]:
        body, status = asyncio.run(do_llm(_by_id({}), _by_umo({}), payload))
        assert status == 400 and body["ok"] is False
