"""LLM 委托桥（无 astrbot 依赖，离线可测）。

Chorus 的 `kind=astrbot` 后端把发言委托给 AstrBot **已配好的 provider**（§6.18+/++，S7.1e/3a）：
POST /llm {prompt, system_prompt?, contexts?, provider_id | umo} → provider.text_chat → {ok, text}。

两种取 provider 方式（二选一）：
- `provider_id`（S7.1e）：显式指定某 provider（`context.get_provider_by_id`）。
- `umo`（S7.3a「整 bot 引用」C）：取该 bot 在该群**在用**的 provider（`context.get_using_provider(umo)`）——
  好友 ≡ 一个 AstrBot bot 时，模型跟随该 bot，无需另指 provider_id。

两个解析函数由 main.py 注入，故本模块不 import astrbot——可在 Chorus venv 用假桩单测。
延续 outbound.py 的"纯逻辑 + 注入工厂"模式。
"""

from __future__ import annotations


async def do_llm(get_provider_by_id, get_using_provider, payload) -> tuple[dict, int]:
    """把 prompt 委托给目标 AstrBot provider。返回 (响应体, HTTP 状态码)。

    入参 `payload` 须含 `prompt` + (`provider_id` 或 `umo`)。
    get_provider_by_id(id) / get_using_provider(umo) -> Provider | None（main.py 注入真实
    context 方法；测试注入假桩）。provider.text_chat(...) 回 LLMResponse，取 completion_text。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "请求体须为 JSON 对象"}, 400
    prompt = payload.get("prompt")
    provider_id = payload.get("provider_id")
    umo = payload.get("umo")
    if prompt is None or not (provider_id or umo):
        return {"ok": False, "error": "缺少 prompt 及 provider_id/umo"}, 400

    if provider_id:
        provider, ref = get_provider_by_id(provider_id), {"provider_id": provider_id}
    else:
        provider, ref = get_using_provider(umo), {"umo": umo}
    if provider is None:
        return {"ok": False, "error": f"未找到 provider（{ref}）"}, 404

    resp = await provider.text_chat(
        prompt=prompt,
        contexts=payload.get("contexts") or [],
        system_prompt=payload.get("system_prompt") or "",
    )
    text = getattr(resp, "completion_text", "") or ""
    return {"ok": True, "text": text, **ref}, 200
