"""LLM 委托桥（无 astrbot 依赖，离线可测）。

Chorus 的 `kind=astrbot` 后端把发言委托给 AstrBot **已配好的 provider**（§6.18+，S7.1e）：
POST /llm {provider_id, system_prompt?, contexts?, prompt} → provider.text_chat → {ok, text}。

provider 由 main.py 注入 `get_provider`（= `context.get_provider_by_id`），故本模块不 import
astrbot——可在 Chorus venv 用假桩单测（真实委托在 AstrBot 进程里手动验）。延续 outbound.py 的
"纯逻辑 + 注入工厂"模式。
"""

from __future__ import annotations


async def do_llm(get_provider, payload) -> tuple[dict, int]:
    """把 prompt 委托给 provider_id 对应的 AstrBot provider。返回 (响应体, HTTP 状态码)。

    get_provider(provider_id) -> Provider | None（main.py 注入 context.get_provider_by_id；
    测试注入假桩）。provider.text_chat(prompt, contexts, system_prompt) 回 LLMResponse，
    取其 completion_text 作为文本。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "请求体须为 JSON 对象"}, 400
    provider_id = payload.get("provider_id")
    prompt = payload.get("prompt")
    if not provider_id or prompt is None:
        return {"ok": False, "error": "缺少 provider_id / prompt"}, 400

    provider = get_provider(provider_id)
    if provider is None:
        return {"ok": False, "error": f"未找到 provider 实例：{provider_id!r}"}, 404

    resp = await provider.text_chat(
        prompt=prompt,
        contexts=payload.get("contexts") or [],
        system_prompt=payload.get("system_prompt") or "",
    )
    text = getattr(resp, "completion_text", "") or ""
    return {"ok": True, "text": text, "provider_id": provider_id}, 200
