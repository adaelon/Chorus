"""结构化输出策略：从 LLM 拿一个 pydantic schema 的实例。

模型能力异构（json_schema / 强制 tool_choice / 纯文本 JSON），调用点（frame /
将来的 schedule）只管要 schema，由 method 决定底层路径。`text_json` 是通用兜底。
见技术方案 §6.9。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from enum import Enum
from typing import TypeVar

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .llm import robust_ainvoke

T = TypeVar("T", bound=BaseModel)


class StructuredMethod(str, Enum):
    JSON_SCHEMA = "json_schema"  # response_format 严格 (OpenAI gpt-4o+)
    FUNCTION_CALLING = "function_calling"  # 强制 tool_choice (Anthropic / 工具模型)
    TEXT_JSON = "text_json"  # 纯文本 JSON + 自解析 (通用兜底)


def _extract_json_obj(text: str) -> dict:
    """从 LLM 文本里抽出最外层 JSON 对象（容忍 ```json 围栏 / 思考前言）。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"未返回可解析的 JSON: {text!r}")
    return json.loads(match.group(0))


async def _via_text_json(
    model: ChatOpenAI,
    messages: list[BaseMessage],
    schema: type[T],
    *,
    attempts: int,
) -> T:
    directive = SystemMessage(
        content=(
            "只输出一个 JSON 对象，匹配以下 JSON Schema，不要任何额外文字、解释或围栏。\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
        )
    )
    msgs = [directive, *messages]
    # 解析级重试：kimi 等推理模型偶发只出 reasoning、content 为空（或非 JSON）→ 重新生成。
    # robust_ainvoke 内部已对连接/超时重试；这里多一层对“空/不可解析”重试。
    last_err: Exception | None = None
    for _ in range(max(1, attempts)):
        resp = await robust_ainvoke(model, msgs)
        try:
            return schema.model_validate(_extract_json_obj(str(resp.content or "")))
        except Exception as e:  # noqa: BLE001  空 content / 无 JSON / schema 不符
            last_err = e
    raise last_err  # type: ignore[misc]


def _default_method() -> StructuredMethod:
    # 延迟导入避免与 config 的循环依赖
    from .config import load_llm_settings

    return StructuredMethod(load_llm_settings().structured_method)


async def structured_invoke(
    model: ChatOpenAI,
    messages: Sequence[BaseMessage],
    schema: type[T],
    *,
    method: StructuredMethod | str | None = None,
    attempts: int = 3,
) -> T:
    """按 method（默认读配置）从 LLM 拿一个 schema 实例。

    text_json 通用兜底；json_schema / function_calling 用 langchain 现成封装
    （注意：尚未对支持它们的真实模型 smoke 过，接入新模型时再验）。
    """
    m = StructuredMethod(method) if method is not None else _default_method()
    msgs = list(messages)

    if m is StructuredMethod.TEXT_JSON:
        return await _via_text_json(model, msgs, schema, attempts=attempts)

    structured = model.with_structured_output(schema, method=m.value)
    return await structured.ainvoke(msgs)
