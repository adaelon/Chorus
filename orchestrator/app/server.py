"""可运行入口：用真实 LLM 起扇出服务，供 curl 联调。

    .venv/Scripts/python -m app.server      # 监听 127.0.0.1:8900

默认 AsyncSqliteSaver（lifespan）。节点不注入 → 走真实 LLM。
CLARIFY 在此显式接 live（default_clarifier）——create_app 默认 None（离线测试不打扰），
真实入口才开信心自评，故离线 e2e 不会误触发真实 LLM 调用。
"""

from __future__ import annotations

import uvicorn

from .llm import make_chat_model
from .nodes.clarify import default_clarifier
from .nodes.plan import default_planner
from .nodes.synthesize import default_composer
from .recipes import default_recipe_planner, default_recipe_selector
from .service import create_app

_model = make_chat_model()
# extract/pick 不在此显式注入：节点默认懒构建真实 LLM（同 assign/generate）。
app = create_app(
    clarify_assess=default_clarifier(_model),
    compose=default_composer(_model),
    planner=default_planner(_model),  # L3 auto：供 /recipe/run 跑库内 auto 配方（S5.4.2b）
    recipe_selector=default_recipe_selector(_model),  # L2 荐配方（S5.1）
    recipe_planner=default_recipe_planner(_model),  # L3 AI 搭配方（S5.5）
)


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8900)


if __name__ == "__main__":
    main()
