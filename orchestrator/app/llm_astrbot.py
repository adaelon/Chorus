"""kind=astrbot 后端：把发言委托给 AstrBot 已配好的 provider（经 group_relay 桥 POST /llm）。

§6.18+（S7.1e）：LLMBackend kind 化，让"这个好友用 AstrBot 的配置"成立。`AstrBotChatModel`
实现 LangChain 的 `astream` 接口（generate/turn 只认 astream），故能无缝插进 `ModelProvider`，
与 ChatOpenAI 并列。**MVP 非流式**：调一次桥拿全文，astream 只吐一个 chunk（流式
text_chat_stream + SSE 桥后补，见 §6.18+ 取舍）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk

from .llm import make_chat_model_from_backend


def _messages_to_payload(messages) -> dict:
    """LangChain messages → AstrBot text_chat 入参：system_prompt + contexts + 末条 prompt。"""
    systems: list[str] = []
    ctx: list[dict] = []
    for m in messages:
        role = getattr(m, "type", "")
        content = m.content if isinstance(m.content, str) else str(m.content)
        if role == "system":
            systems.append(content)
        elif role == "human":
            ctx.append({"role": "user", "content": content})
        elif role == "ai":
            ctx.append({"role": "assistant", "content": content})
    prompt = ""
    if ctx and ctx[-1]["role"] == "user":  # 末条用户消息作 prompt，其余作 contexts
        prompt = ctx.pop()["content"]
    return {"system_prompt": "\n".join(systems), "contexts": ctx, "prompt": prompt}


class AstrBotChatModel:
    """把 astream 委托给 AstrBot provider（经桥）。`send` 可注入以离线测（不碰网络）。"""

    def __init__(self, provider_id: str, bridge_url: str, *, send=None, timeout: float = 600.0) -> None:
        if not provider_id:
            raise ValueError("kind=astrbot 后端需填 provider_id")
        self.provider_id = provider_id
        self._url = bridge_url.rstrip("/") + "/llm"
        self._timeout = timeout
        self._send = send  # async (url, payload) -> dict

    async def _http_send(self, url: str, payload: dict) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def astream(self, messages, config=None):
        payload = {"provider_id": self.provider_id, **_messages_to_payload(messages)}
        send = self._send or self._http_send
        body = await send(self._url, payload)
        if isinstance(body, dict) and not body.get("ok", True):
            raise RuntimeError(f"astrbot provider 委托失败：{body.get('error')}")
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        yield AIMessageChunk(content=text)


def make_model_from_backend(backend: Any, *, bridge_url: str) -> Any:
    """按 `LLMBackend.kind` 造模型：openai→ChatOpenAI；astrbot→AstrBotChatModel（桥委托）。

    kind 缺省/未知按 openai（向后兼容 S7.1a-d 的纯 openai 后端）。
    """
    kind = (getattr(backend, "kind", "") or "openai").lower()
    if kind == "astrbot":
        return AstrBotChatModel(getattr(backend, "provider_id", "") or "", bridge_url)
    return make_chat_model_from_backend(backend)
