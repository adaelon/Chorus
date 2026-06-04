"""S7.1a 判据：LLMBackend 注册表 CRUD + api_key 走环境变量引用（§6.18）。

每好友独立模型的引用目标：CRUD 持久；构造 model 时 api_key 从 `api_key_env` 指向的
环境变量读——缺失则清晰报错（key 不落库、不进 git）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.llm import MissingApiKeyEnv, make_chat_model_from_backend
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
                  "api_key_env": "OPENAI_KEY", "model": "gpt-4o"},
        )
        assert r.status_code == 200 and r.json()["name"] == "GPT-4o"
        # 明文 key 不该被接收/落库：只有 api_key_env 字段
        assert "api_key" not in r.json() and r.json()["api_key_env"] == "OPENAI_KEY"
        # duplicate → 409
        assert client.post("/llm-backends", json={"id": "gpt", "name": "x"}).status_code == 409
        # list
        assert any(b["id"] == "gpt" for b in client.get("/llm-backends").json())
        # update
        r = client.put(
            "/llm-backends/gpt",
            json={"id": "gpt", "name": "GPT-4o", "api_key_env": "OPENAI_KEY",
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
        self.api_key_env = kw.get("api_key_env", "")
        self.model = kw.get("model", "m")
        self.temperature = kw.get("temperature", 0.75)
        self.max_tokens = kw.get("max_tokens", None)


def test_make_model_missing_api_key_env_raises(monkeypatch):
    """api_key_env 指向的环境变量缺失 → MissingApiKeyEnv，含后端名与变量名。"""
    monkeypatch.delenv("DEEPSEEK_KEY", raising=False)
    b = _Backend(name="DeepSeek", api_key_env="DEEPSEEK_KEY")
    with pytest.raises(MissingApiKeyEnv) as ei:
        make_chat_model_from_backend(b)
    msg = str(ei.value)
    assert "DeepSeek" in msg and "DEEPSEEK_KEY" in msg

    # 空 api_key_env 同样报错
    with pytest.raises(MissingApiKeyEnv):
        make_chat_model_from_backend(_Backend(api_key_env=""))


def test_make_model_reads_key_from_env(monkeypatch):
    """api_key_env 命中环境变量 → 构造出 ChatOpenAI，key 取自环境（不落库）。"""
    monkeypatch.setenv("DEEPSEEK_KEY", "sk-live-xyz")
    monkeypatch.setenv("LLM_STREAM_CHUNK_TIMEOUT", "0")  # 禁看门狗，避免依赖完整 LLM_* 环境
    b = _Backend(name="DeepSeek", api_key_env="DEEPSEEK_KEY",
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


def test_test_endpoint_missing_key(tmp_path, monkeypatch):
    """key 缺失 → {ok:False}，错误含变量名（不抛 5xx）。"""
    monkeypatch.delenv("GHOST_KEY", raising=False)
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/llm-backends/test", json={"name": "X", "base_url": "https://x/v1", "api_key_env": "GHOST_KEY", "model": "m"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False and "GHOST_KEY" in body["error"]


def test_test_endpoint_ping_paths(tmp_path, monkeypatch):
    """env 命中 → 成功路（回显 reply）；ping 抛错 → {ok:False,error}。"""
    monkeypatch.setenv("GOOD_KEY", "sk-x")

    async def _ok(model, **kw):
        return "PONG"

    monkeypatch.setattr("app.service.ping_model", _ok)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/test", json={"base_url": "https://x/v1", "api_key_env": "GOOD_KEY", "model": "m"}).json()
        assert body["ok"] is True and body["reply"] == "PONG"

    async def _boom(model, **kw):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr("app.service.ping_model", _boom)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/test", json={"base_url": "https://x/v1", "api_key_env": "GOOD_KEY", "model": "m"}).json()
        assert body["ok"] is False and "401" in body["error"]


def test_probe_models_endpoint(tmp_path, monkeypatch):
    """env 命中 → 返回模型列表；key 缺失 → {ok:False} 含变量名。"""
    monkeypatch.setenv("GOOD_KEY", "sk-x")

    async def _models(base_url, key, **kw):
        return ["gpt-4o", "gpt-4o-mini"]

    monkeypatch.setattr("app.service.probe_models", _models)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/probe-models", json={"base_url": "https://x/v1", "api_key_env": "GOOD_KEY"}).json()
        assert body["ok"] is True and body["models"] == ["gpt-4o", "gpt-4o-mini"]

    monkeypatch.delenv("NOPE_KEY", raising=False)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/llm-backends/probe-models", json={"base_url": "https://x/v1", "api_key_env": "NOPE_KEY"}).json()
        assert body["ok"] is False and "NOPE_KEY" in body["error"]
