"""可运行入口：用真实 LLM 起扇出服务，供 curl 联调。

    .venv/Scripts/python -m app.server      # 监听 127.0.0.1:8900

默认 AsyncSqliteSaver（lifespan）。节点不注入 → 走真实 LLM。
CLARIFY 在此显式接 live（default_clarifier）——create_app 默认 None（离线测试不打扰），
真实入口才开信心自评，故离线 e2e 不会误触发真实 LLM 调用。
"""

from __future__ import annotations

import os

import uvicorn

from .execution_opensandbox import OpenSandboxBackend
from .llm import make_chat_model
from .nodes.clarify import default_clarifier
from .nodes.plan import default_planner
from .nodes.synthesize import default_composer, default_produce_composer
from .recipes import default_recipe_planner, default_recipe_selector
from .service import create_app

_model = make_chat_model()


def _execution_kwargs() -> dict:
    """开 execution：圆桌每个 AI 一轮可先用工具（沙箱跑代码 / MCP）再发言（§6.24，β/门控）。

    `CHORUS_SANDBOX_DOMAIN` → 真 OpenSandbox 沙箱；`CHORUS_EXECUTION=1` → 仅 MCP（无沙箱）。
    两者皆缺 → 不启用，圆桌纯发言照旧（单机/纯产品路径不依赖工具）。
    plan_model 让 create_app 据 MCP 注册表（DB）建真 planner 流（工具目录 = 沙箱 + 各 MCP server）。
    """
    domain = os.getenv("CHORUS_SANDBOX_DOMAIN")
    if not domain and os.getenv("CHORUS_EXECUTION") != "1":
        print("[chorus] execution disabled — set CHORUS_SANDBOX_DOMAIN or CHORUS_EXECUTION=1 to enable")
        return {}
    kw: dict = {"plan_model": _model}  # S13f.b：create_app 据此 + MCP 目录建 plan_stream
    if domain:
        kw["sandbox_backend"] = OpenSandboxBackend(
            domain=domain, api_key=os.getenv("CHORUS_SANDBOX_API_KEY", "")
        )
        print(f"[chorus] execution enabled — sandbox: {domain}, MCP: from DB registry")
    else:
        print("[chorus] execution enabled — MCP only (no sandbox)")
    return kw


# extract/pick 不在此显式注入：节点默认懒构建真实 LLM（同 assign/generate）。
app = create_app(
    clarify_assess=default_clarifier(_model),
    compose=default_composer(_model),
    compose_produce=default_produce_composer(_model),  # S10a 出产物主笔（§6.21）
    planner=default_planner(_model),  # L3 auto：供 /recipe/run 跑库内 auto 配方（S5.4.2b）
    recipe_selector=default_recipe_selector(_model),  # L2 荐配方（S5.1）
    recipe_planner=default_recipe_planner(_model),  # L3 AI 搭配方（S5.5）
    **_execution_kwargs(),  # S13d：env 配了 sandbox → 圆桌 turn 工具化
)


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8900)


if __name__ == "__main__":
    main()
