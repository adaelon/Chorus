"""运行期上下文：把"当前会话 group_key"传到底层模型（S7.3b 整 bot 引用需按 umo 委托）。

`generate → model.astream` 链路不显式带 group_key；用 ContextVar 在节点（turn/fanout）处注入，
`AstrBotChatModel`（follow-bot 模式）读取以构造 bot-umo（= group_key 平台段换成 bot_ref）。
ContextVar 随 asyncio 任务复制上下文，并发会话各自独立、互不串。
"""

from __future__ import annotations

import contextvars

current_group_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chorus_current_group_key", default=None
)
