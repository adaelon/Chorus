"""可运行入口：用真实 LLM 起扇出服务，供 curl 联调。

    .venv/Scripts/python -m app.server      # 监听 127.0.0.1:8900

默认 MemorySaver（会话内持久）。节点不注入 → 走真实 LLM。
"""

from __future__ import annotations

import uvicorn

from .service import create_app

app = create_app()


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8900)


if __name__ == "__main__":
    main()
