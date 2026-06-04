"""S7.1e 判据（离线纯逻辑）：/llm 委托——按 provider_id 选 provider、text_chat、回 completion_text。

只测 llm_bridge.py（无 astrbot 依赖）：假 get_provider + 假 provider 桩。真实"Chorus kind=astrbot
后端 → 桥 → AstrBot provider 发言"在 AstrBot 进程里手动验（需配 provider）。
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


def _get_provider(insts: dict):
    return lambda pid: insts.get(pid)


def test_llm_delegates_to_provider():
    prov = _FakeProvider()
    payload = {
        "provider_id": "p1",
        "system_prompt": "你是A",
        "contexts": [{"role": "user", "content": "前文"}],
        "prompt": "问题",
    }
    body, status = asyncio.run(do_llm(_get_provider({"p1": prov}), payload))
    assert status == 200 and body["ok"] is True
    assert body["text"] == "[你是A] 问题" and body["provider_id"] == "p1"
    assert prov.calls == [{"prompt": "问题", "contexts": [{"role": "user", "content": "前文"}], "system_prompt": "你是A"}]


def test_llm_unknown_provider_returns_404():
    body, status = asyncio.run(do_llm(_get_provider({}), {"provider_id": "ghost", "prompt": "q"}))
    assert status == 404 and body["ok"] is False


def test_llm_bad_payload_returns_400():
    for payload in [{}, {"provider_id": "p"}, {"prompt": "q"}, "notdict"]:
        body, status = asyncio.run(do_llm(_get_provider({}), payload))
        assert status == 400 and body["ok"] is False
