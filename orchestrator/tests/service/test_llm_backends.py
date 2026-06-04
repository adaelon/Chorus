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
