"""入站核心逻辑（无 astrbot 依赖，离线可测）。

群消息钩子拿到一条消息 → 规范化成 InboundMsg → POST 编排服务。两个坑（技术方案 §2.1）：
  ① N 个 bot 在同群各收到同一条人类消息 → 按 (group_key, native_msg_id) 去重，先到先得。
  ② 不截断的话 AstrBot 会用自己的 provider 自动回复 → 必须 stop_event（在 main.py 里做）。

本模块只放纯逻辑（去重 / 转发决策 / 规范化），astrbot 字段由 main.py 从 event 取出后传入，
故离线可用假数据单测。
"""

from __future__ import annotations

from collections import deque


class Dedup:
    """(group_key, native_msg_id) 去重，先到先得；有界，防长跑内存无限增长。"""

    def __init__(self, maxlen: int = 2000) -> None:
        self._seen: set = set()
        self._order: deque = deque()
        self._maxlen = maxlen

    def seen_before(self, key) -> bool:
        """首次见到返回 False（并记下）；已见过返回 True。"""
        if key in self._seen:
            return True
        self._seen.add(key)
        self._order.append(key)
        if len(self._order) > self._maxlen:
            self._seen.discard(self._order.popleft())
        return False


def decide(*, group_key, msg_id, sender_id, self_id, text, dedup: Dedup) -> str:
    """决定如何处置一条群消息：

      "forward"   → 规范化并转发给大脑，然后 stop_event
      "stop_only" → 同一条已转发过（其它 bot 收到的副本）→ 只 stop_event 防自动回复
      "ignore"    → 自己发的 / 空文本 / 缺关键字段 → 不转发也不截断
    """
    if not group_key or not msg_id:
        return "ignore"
    if sender_id and self_id and sender_id == self_id:
        return "ignore"  # 自己（本 bot）发的，不回流（多 bot 间 AI 识别留 S4.3）
    if not (text or "").strip():
        return "ignore"
    if dedup.seen_before((group_key, msg_id)):
        return "stop_only"
    return "forward"


def make_inbound_msg(
    *, group_key, platform, sender_id, sender_name, sender_kind, text, native_msg_id, ts
) -> dict:
    """规范化消息（编排服务的入站契约，技术方案 §2.1 InboundMsg）。"""
    return {
        "group_key": group_key,
        "platform": platform,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_kind": sender_kind,
        "text": text,
        "native_msg_id": native_msg_id,
        "ts": ts,
    }
