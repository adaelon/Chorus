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
from .run_ctx import current_group_key

# 注：S7.3b 的 `@bot` 哨兵已被 S7.4 的 kind=astrbot_bot 后端取代（follow-bot 机制本身保留）。


def _bot_umo(bot_ref: str, group_key: str) -> str:
    """把 group_key 的平台段换成 bot_ref → 该 bot 在该群的 umo（同 outbound 出站口径）。"""
    parts = (group_key or "").split(":", 2)
    if len(parts) == 3:
        return f"{bot_ref}:{parts[1]}:{parts[2]}"
    return group_key  # 非三段，兜底原样


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
    """把 astream 委托给 AstrBot provider（经桥）。`send` 可注入以离线测（不碰网络）。

    两种模式（二选一）：
    - `provider_id`（S7.1e）：显式 provider，桥按 id 取。
    - `bot_ref`（S7.3b 整 bot 引用）：模型跟随该 bot——调用时按 `run_ctx.current_group_key` 构造
      bot-umo（平台段换成 bot_ref），桥按 umo 取该 bot 在该群在用的 provider。
    """

    def __init__(
        self,
        provider_id: str = "",
        bridge_url: str = "",
        *,
        bot_ref: str | None = None,
        send=None,
        timeout: float = 600.0,
    ) -> None:
        if not provider_id and not bot_ref:
            raise ValueError("AstrBotChatModel 需 provider_id 或 bot_ref 之一")
        self.provider_id = provider_id
        self.bot_ref = bot_ref
        self._url = bridge_url.rstrip("/") + "/llm"
        self._timeout = timeout
        self._send = send  # async (url, payload) -> dict

    async def _http_send(self, url: str, payload: dict) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    def _route(self) -> dict:
        """选 provider 的入参：provider_id 直给；follow-bot 按当前会话 group_key 构造 bot-umo。"""
        if self.provider_id:
            return {"provider_id": self.provider_id}
        gk = current_group_key.get()
        if not gk:
            raise RuntimeError("follow-bot 模型缺 group_key（节点未注入 run_ctx.current_group_key）")
        return {"umo": _bot_umo(self.bot_ref or "", gk)}

    async def astream(self, messages, config=None):
        payload = {**self._route(), **_messages_to_payload(messages)}
        send = self._send or self._http_send
        body = await send(self._url, payload)
        if isinstance(body, dict) and not body.get("ok", True):
            raise RuntimeError(f"astrbot provider 委托失败：{body.get('error')}")
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        yield AIMessageChunk(content=text)


def make_model_from_backend(backend: Any, *, bridge_url: str) -> Any:
    """按 `LLMBackend.kind` 造模型：
    - openai → ChatOpenAI（缺省/未知亦按此，向后兼容）
    - astrbot → AstrBotChatModel（显式 provider_id，S7.1e）
    - astrbot_bot → AstrBotChatModel（整 bot：模型跟随该 bot 的 provider，S7.4a；通道由 S7.4b 取 bot_id）
    """
    kind = (getattr(backend, "kind", "") or "openai").lower()
    if kind == "astrbot_bot":
        return AstrBotChatModel(bridge_url=bridge_url, bot_ref=getattr(backend, "bot_id", "") or "")
    if kind == "astrbot":
        return AstrBotChatModel(getattr(backend, "provider_id", "") or "", bridge_url)
    return make_chat_model_from_backend(backend)
