"""S1.2: LLM 客户端封装 + retry。

只封装"如何稳健地调一次 LLM"，不含任何编排 / 节点逻辑（见 S1.3+）。
治标：实测后端偶发 APIConnectionError（技术方案 §8 / 需求 §9.8），retry 即恢复。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import openai
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import _parse_chunk_timeout, load_llm_settings

# 可重试的瞬时错误（连接抖动 / 超时 / 5xx）；4xx 业务错误不重试
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


def make_chat_model(**overrides: Any) -> ChatOpenAI:
    """按配置造一个 ChatOpenAI（OpenAI 兼容 /v1 后端）。"""
    s = load_llm_settings()
    params: dict[str, Any] = {
        "base_url": s.base_url,
        "api_key": s.api_key,
        "model": s.model,
        "temperature": s.temperature,
        # 放宽流式 chunk 间隔看门狗（默认 120s 会误杀 kimi 推理间隙）；None=禁用。
        "stream_chunk_timeout": s.stream_chunk_timeout,
    }
    if s.max_tokens:
        params["max_tokens"] = s.max_tokens
    params.update(overrides)
    return ChatOpenAI(**params)


class MissingApiKey(RuntimeError):
    """LLM 后端未填 api_key（kind=openai 必填；直接粘贴存本地 DB）。"""


def make_chat_model_from_backend(backend: Any, **overrides: Any) -> ChatOpenAI:
    """按 `LLMBackend` 记录造 ChatOpenAI（S7.1a，§6.18：每好友独立模型）。

    api_key 直接取 `backend.api_key`（粘贴存本地 DB，§6.18 修订）；为空抛 `MissingApiKey`（清晰
    报错，含后端名）。`backend` 鸭子类型：需有 name/base_url/api_key/model/temperature/max_tokens。
    """
    who = getattr(backend, "name", "") or getattr(backend, "id", "?")
    api_key = getattr(backend, "api_key", "") or ""
    if not api_key:
        raise MissingApiKey(f"LLM 后端 {who!r} 未填 API Key（请在「模型」页粘贴）。")
    params: dict[str, Any] = {
        "base_url": backend.base_url,
        "api_key": api_key,
        "model": backend.model,
        "temperature": getattr(backend, "temperature", 0.75),
        # 沿用全局看门狗设置（kimi 推理间隙），不强依赖完整 LLM_* 环境变量。
        "stream_chunk_timeout": _parse_chunk_timeout(),
    }
    if getattr(backend, "max_tokens", None):
        params["max_tokens"] = backend.max_tokens
    params.update(overrides)
    return ChatOpenAI(**params)


def robust_invoke(
    model: ChatOpenAI,
    messages: Sequence[BaseMessage],
    *,
    attempts: int = 3,
    wait_initial: float = 0.5,
) -> BaseMessage:
    """对一次 model.invoke 包 retry；attempts 次后仍失败抛原始异常。"""

    @retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=wait_initial, max=8),
        retry=retry_if_exception_type(RETRYABLE),
    )
    def _call() -> BaseMessage:
        return model.invoke(messages)

    return _call()


async def robust_ainvoke(
    model: ChatOpenAI,
    messages: Sequence[BaseMessage],
    *,
    attempts: int = 3,
    wait_initial: float = 0.5,
    config: dict | None = None,
) -> BaseMessage:
    """流式累积一次调用，返回完整 AIMessage（供 FANOUT/structured 等用）。

    用 `astream` 而非 `ainvoke`：实测本后端的 reasoning 模型（如 kimi-k2.6）非流式
    会空返回 / 长时间不吐字直到上游连接重置；流式则首字节快、可靠（需求 §9.8）。
    `config`（tags/metadata）透传给 astream，供 LangGraph messages 流按 agent 路由。
    retry 在整段流上重试（失败则从头重新 astream）。
    """

    @retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=wait_initial, max=8),
        retry=retry_if_exception_type(RETRYABLE),
    )
    async def _call() -> BaseMessage:
        parts: list[str] = []
        async for chunk in model.astream(messages, config=config):
            content = getattr(chunk, "content", "")
            if content:
                parts.append(content if isinstance(content, str) else str(content))
        return AIMessage(content="".join(parts))

    return await _call()


async def ping_model(model: ChatOpenAI, *, timeout: float = 45.0) -> str:
    """打一句最小 prompt 验活（仿 AstrBot provider.test()），返回回包文本；异常/超时上抛。

    S7.1d 配置可验证：测试一个后端是否真能对话（key/base_url/model 三者齐全且可达）。
    单次（attempts=1）不重试——测试要快速暴露问题，不掩盖瞬时错误。
    """
    msg = await asyncio.wait_for(
        robust_ainvoke(model, [HumanMessage(content="REPLY `PONG` ONLY")], attempts=1),
        timeout=timeout,
    )
    return str(msg.content)


async def probe_models(base_url: str, api_key: str, *, timeout: float = 15.0) -> list[str]:
    """拉 OpenAI 兼容后端的模型列表（`GET {base_url}/models`），返回模型 id 列表（S7.1d）。

    失败（网络/鉴权/非 OpenAI 兼容）上抛异常，由端点转成清晰错；前端拉不到可回退手填。
    """
    import httpx

    url = base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        data = resp.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
