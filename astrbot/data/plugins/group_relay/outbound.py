"""出站核心逻辑（无 astrbot 依赖，离线可测）。

编排服务决定"以 bot X 身份在某群说话"→ POST /outbound {group_key, bot_id, text}。
本模块只做**纯逻辑**：解析 group_key、按 bot_id 选平台实例、构造会话、发消息。astrbot
的具体类型（MessageSession/MessageChain/Plain）由 main.py 注入 `make_session`/`make_chain`，
故这里不 import astrbot——可在 Chorus venv 用假桩单测（真实收发在 AstrBot 进程里手动验）。

group_key = AstrBot unified_msg_origin = "{platform_id}:{message_type}:{session_id}"。
出站要"以 bot X 发"，故把会话的平台段换成 bot_id（= Contact.bot_ref = 某 platform 实例 id），
session_id/message_type 沿用原群。
"""

from __future__ import annotations


def parse_target(group_key: str, bot_id: str) -> tuple[str, str, str]:
    """从 group_key + bot_id 解析出 (bot_id, message_type, session_id)。

    group_key 必须是 "platform:type:session" 三段；平台段被丢弃（改用 bot_id 发）。
    """
    parts = (group_key or "").split(":", 2)
    if len(parts) != 3 or not all(p for p in parts):
        raise ValueError(
            f"非法 group_key（需 platform:type:session 三段）：{group_key!r}"
        )
    if not bot_id:
        raise ValueError("bot_id 不能为空")
    _platform_id, message_type, session_id = parts
    return bot_id, message_type, session_id


async def do_outbound(context, cmd, *, make_session, make_chain) -> tuple[dict, int]:
    """以 bot_id 实例向 group_key 群发出 text。返回 (响应体, HTTP 状态码)。

    context：AstrBot Star 的 Context（用 get_platform_inst 取实例）。
    make_session(bot_id, message_type, session_id) / make_chain(text)：astrbot 类型工厂
    （main.py 注入真实实现；测试注入假桩）。
    """
    if not isinstance(cmd, dict):
        return {"ok": False, "error": "请求体须为 JSON 对象"}, 400
    group_key = cmd.get("group_key")
    bot_id = cmd.get("bot_id")
    text = cmd.get("text")
    if not group_key or not bot_id or text is None:
        return {"ok": False, "error": "缺少 group_key / bot_id / text"}, 400

    try:
        bid, message_type, session_id = parse_target(group_key, bot_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400

    platform = context.get_platform_inst(bid)
    if platform is None:
        return {"ok": False, "error": f"未找到 bot/平台实例：{bid!r}"}, 404

    session = make_session(bid, message_type, session_id)
    await platform.send_by_session(session, make_chain(text))
    return {"ok": True, "bot_id": bid, "session": f"{bid}:{message_type}:{session_id}"}, 200
