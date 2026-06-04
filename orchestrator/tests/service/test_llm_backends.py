"""S7.1a 判据：LLMBackend 注册表 CRUD + api_key 直接粘贴存本地 DB（§6.18 修订）。

每好友独立模型的引用目标：CRUD 持久；构造 model 时 api_key 直接取 `backend.api_key`
（粘贴存本地 DB，DB 已 gitignore）——未填则清晰报错。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.llm import MissingApiKey, make_chat_model_from_backend
from app.service import create_app


def _app(tmp_path):
    return create_app(
        checkpointer=MemorySaver(),
        registry_db_path=str(tmp_path / "reg.sqlite"),
    )


def test_llm_backend_crud(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        # create
        r = client.post(
            "/llm-backends",
            json={"id": "gpt", "name": "GPT-4o", "base_url": "https://api.openai.com/v1",
                  "api_key": "sk-xyz", "model": "gpt-4o"},
        )
        assert r.status_code == 200 and r.json()["name"] == "GPT-4o"
        # api_key 直接存（DB 已 gitignore，本地自用）
        assert r.json()["api_key"] == "sk-xyz"
        # duplicate → 409
        assert client.post("/llm-backends", json={"id": "gpt", "name": "x"}).status_code == 409
        # list
        assert any(b["id"] == "gpt" for b in client.get("/llm-backends").json())
        # update
        r = client.put(
            "/llm-backends/gpt",
            json={"id": "gpt", "name": "GPT-4o", "api_key": "sk-xyz",
                  "model": "gpt-4o-mini", "temperature": 0.5},
        )
        assert r.status_code == 200 and r.json()["model"] == "gpt-4o-mini" and r.json()["temperature"] == 0.5
        # update 不存在 → 404
        assert client.put("/llm-backends/nope", json={"id": "nope"}).status_code == 404
        # delete
        assert client.delete("/llm-backends/gpt").status_code == 200
        assert client.delete("/llm-backends/gpt").status_code == 404  # 已删


class _Backend:
    """鸭子型后端记录（CRUD 返回的 LLMBackend 等价）。"""

    def __init__(self, **kw):
        self.id = kw.get("id", "b")
        self.name = kw.get("name", "")
        self.base_url = kw.get("base_url", "https://x/v1")
        self.api_key = kw.get("api_key", "")
        self.model = kw.get("model", "m")
        self.temperature = kw.get("temperature", 0.75)
        self.max_tokens = kw.get("max_tokens", None)


def test_make_model_missing_api_key_raises():
    """未填 api_key → MissingApiKey，含后端名。"""
    with pytest.raises(MissingApiKey) as ei:
        make_chat_model_from_backend(_Backend(name="DeepSeek", api_key=""))
    assert "DeepSeek" in str(ei.value)


def test_make_model_uses_api_key(monkeypatch):
    """直接粘贴的 api_key → 构造出 ChatOpenAI，key 即所填。"""
    monkeypatch.setenv("LLM_STREAM_CHUNK_TIMEOUT", "0")  # 禁看门狗，避免依赖完整 LLM_* 环境
    b = _Backend(name="DeepSeek", api_key="sk-live-xyz",
                 base_url="https://api.deepseek.com/v1", model="deepseek-chat", max_tokens=256)
    model = make_chat_model_from_backend(b)
    assert model.model_name == "deepseek-chat"
    assert model.openai_api_key.get_secret_value() == "sk-live-xyz"


# ---- S7.1d 配置可验证：测试端点 + 拉模型列表 ----


class _FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def astream(self, messages, config=None):
        async def gen():
            yield _FakeChunk(self.tag)

        return gen()


async def test_ping_model_fake():
    """ping_model 走 astream 累积，返回回包文本（不碰网络）。"""
    from app.llm import ping_model

    assert await ping_model(_FakeModel("PONG")) == "PONG"


def test_test_endpoint_missing_key(tmp_path):
    """未填 key → {ok:False}，错误含 'API Key'（不抛 5xx）。"""
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/llm-backends/test", json={"name": "X", "base_url": "https://x/v1", "api_key": "", "model": "m"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False and "API Key" in body["error"]


def test_test_endpoint_ping_paths(tmp_path, monkeypatch):
    """有 key → 成功路（回显 reply）；ping 抛错 → {ok:False,error}。"""

    async def _ok(model, **kw):
        return "PONG"

    monkeypatch.setattr("app.service.ping_model", _ok)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/test", json={"base_url": "https://x/v1", "api_key": "sk-x", "model": "m"}).json()
        assert body["ok"] is True and body["reply"] == "PONG"

    async def _boom(model, **kw):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr("app.service.ping_model", _boom)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/test", json={"base_url": "https://x/v1", "api_key": "sk-x", "model": "m"}).json()
        assert body["ok"] is False and "401" in body["error"]


def test_probe_models_endpoint(tmp_path, monkeypatch):
    """有 key → 返回模型列表；未填 key → {ok:False} 含 'API Key'。"""

    async def _models(base_url, key, **kw):
        return ["gpt-4o", "gpt-4o-mini"]

    monkeypatch.setattr("app.service.probe_models", _models)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/probe-models", json={"base_url": "https://x/v1", "api_key": "sk-x"}).json()
        assert body["ok"] is True and body["models"] == ["gpt-4o", "gpt-4o-mini"]

    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/probe-models", json={"base_url": "https://x/v1", "api_key": ""}).json()
        assert body["ok"] is False and "API Key" in body["error"]
