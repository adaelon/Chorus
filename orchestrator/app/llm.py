"""S1.2: LLM 客户端封装 + retry。

只封装"如何稳健地调一次 LLM"，不含任何编排 / 节点逻辑（见 S1.3+）。
治标：实测后端偶发 APIConnectionError（技术方案 §8 / 需求 §9.8），retry 即恢复。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import openai
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import load_llm_settings

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
    }
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
