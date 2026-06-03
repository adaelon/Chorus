"""配置加载。

优先读 os.environ；缺失时从 **Chorus 自己的 `orchestrator/.env`** 补（不入库，
见 .env.example）。生产环境应直接注入真实环境变量。可用 CHORUS_DOTENV 覆盖路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Chorus/orchestrator/app/config.py -> orchestrator/.env
_DEFAULT_DOTENV = Path(__file__).resolve().parents[1] / ".env"


def _load_env() -> None:
    dotenv_path = os.environ.get("CHORUS_DOTENV", str(_DEFAULT_DOTENV))
    # override=False：已存在的真实环境变量优先，不被 .env 覆盖
    load_dotenv(dotenv_path, override=False)


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.75
    # 结构化输出策略：见 app/structured.py。默认 text_json（通用兜底）。
    # 当前后端 deepseek-v4-pro 不支持 json_schema / 强制 tool_choice，故默认即正确。
    structured_method: str = "text_json"
    # 可选输出上限（控延迟/成本）；None=不限。LLM_MAX_TOKENS。
    max_tokens: int | None = None
    # 流式 chunk 间隔看门狗（秒）：两个 chunk 间超此值报错。kimi 等推理模型在 reasoning→正文
    # 间隙可能 >120s（langchain 默认），故放宽。LLM_STREAM_CHUNK_TIMEOUT；0/none=禁用。
    stream_chunk_timeout: float | None = 600.0


def _parse_chunk_timeout() -> float | None:
    raw = os.environ.get("LLM_STREAM_CHUNK_TIMEOUT")
    if raw is None:
        return 600.0
    if raw.strip().lower() in ("0", "none", ""):
        return None  # 禁用看门狗
    return float(raw)


def load_llm_settings() -> LLMSettings:
    _load_env()
    raw_max = os.environ.get("LLM_MAX_TOKENS")
    return LLMSettings(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.75")),
        structured_method=os.environ.get("LLM_STRUCTURED_METHOD", "text_json"),
        max_tokens=int(raw_max) if raw_max else None,
        stream_chunk_timeout=_parse_chunk_timeout(),
    )
