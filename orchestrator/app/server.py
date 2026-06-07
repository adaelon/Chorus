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
from .nodes.plan_stream import default_plan_stream
from .nodes.synthesize import default_composer, default_produce_composer
from .recipes import default_recipe_planner, default_recipe_selector
from .service import create_app

_model = make_chat_model()


def _execution_kwargs() -> dict:
    """S13d：配了 `CHORUS_SANDBOX_DOMAIN` → 开 execution（真 OpenSandbox + 真 planner 流）。

    全员可用：圆桌每个 AI 一轮可先用工具（沙箱跑代码）再发言（§6.24，β/门控）。
    env 缺 → 不启用，圆桌纯发言照旧（单机/纯产品路径不依赖沙箱）。
    """
    domain = os.getenv("CHORUS_SANDBOX_DOMAIN")
    if not domain:
        return {}
    return {
        "execution_stream": default_plan_stream(_model),
        "sandbox_backend": OpenSandboxBackend(
            domain=domain, api_key=os.getenv("CHORUS_SANDBOX_API_KEY", "")
        ),
    }


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
