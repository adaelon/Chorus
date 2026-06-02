"""S4.2 判据（离线纯逻辑）：去重(同 msg_id 两次只转一次) + 转发决策 + 规范化。

只测 inbound.py（无 astrbot 依赖）。"无自动回复"(stop_event) 在 AstrBot 进程里手动验。
运行： orchestrator/.venv/Scripts/python -m pytest astrbot/data/plugins/group_relay/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 插件目录

from inbound import Dedup, decide, make_inbound_msg  # noqa: E402


# ---- Dedup ----


def test_dedup_first_time_false_then_true():
    d = Dedup()
    key = ("g1", "m1")
    assert d.seen_before(key) is False  # 首见
    assert d.seen_before(key) is True   # 再见=重复


def test_dedup_distinct_keys_independent():
    d = Dedup()
    assert d.seen_before(("g1", "m1")) is False
    assert d.seen_before(("g1", "m2")) is False
    assert d.seen_before(("g2", "m1")) is False


def test_dedup_is_bounded():
    d = Dedup(maxlen=3)
    for i in range(3):
        d.seen_before(("g", f"m{i}"))
    d.seen_before(("g", "m3"))  # 触发淘汰最早的 m0
    assert d.seen_before(("g", "m0")) is False  # m0 被淘汰 → 再视为首见


# ---- decide：同一 msg_id 两次只转发一次 ----


def test_same_msg_id_forwarded_once_then_stop_only():
    d = Dedup()
    common = dict(group_key="g1", msg_id="m1", sender_id="u", self_id="bot", text="hi", dedup=d)
    assert decide(**common) == "forward"     # 第一个 bot 收到 → 转发
    assert decide(**common) == "stop_only"   # 第二个 bot 收到同一条 → 不再转发，仅截断


def test_decide_ignores_self_empty_and_missing():
    d = Dedup()
    base = dict(group_key="g", msg_id="m", sender_id="u", self_id="bot", text="hi", dedup=d)
    assert decide(**{**base, "sender_id": "bot"}) == "ignore"   # 自己发的
    assert decide(**{**base, "text": "   "}) == "ignore"        # 空白
    assert decide(**{**base, "group_key": ""}) == "ignore"      # 缺 group_key
    assert decide(**{**base, "msg_id": ""}) == "ignore"         # 缺 msg_id


# ---- make_inbound_msg ----


def test_make_inbound_msg_shape():
    msg = make_inbound_msg(
        group_key="telegram_main:GroupMessage:42",
        platform="telegram",
        sender_id="u1",
        sender_name="小明",
        sender_kind="human",
        text="在吗",
        native_msg_id="m9",
        ts=1717300000,
    )
    assert msg == {
        "group_key": "telegram_main:GroupMessage:42",
        "platform": "telegram",
        "sender_id": "u1",
        "sender_name": "小明",
        "sender_kind": "human",
        "text": "在吗",
        "native_msg_id": "m9",
        "ts": 1717300000,
    }
