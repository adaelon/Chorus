"""S3.6f 回归：圆桌 SSE 事件转换器 `_to_roundtable_event` 只把**带 agent tag** 的
turn-node token 当 delta。

bug：turn 节点内除发言生成外还跑中立提点 extractor（structured_invoke→astream 流式），
其 token 同样 langgraph_node=="turn" 但**无 agent tag** → 曾被当成 contact_id=None 的
新发言气泡（前端冒出"总结"气泡 + 随后 turn 事件落重复全文）。修复：无 agent tag 的
turn token 不发 delta（对齐扇出 `_to_event` 的 `and cid` 约束）。
"""

from __future__ import annotations

from app.service import _to_roundtable_event


class _Chunk:
    def __init__(self, content: str):
        self.content = content


def _messages(content: str, *, node: str, tags=None):
    return ("messages", (_Chunk(content), {"langgraph_node": node, "tags": tags or []}))


def test_agent_tagged_turn_token_is_delta():
    mode, payload = _messages("半句话", node="turn", tags=["agent:ada1"])
    ev = _to_roundtable_event(mode, payload)
    assert ev == {"type": "delta", "contact_id": "ada1", "text": "半句话"}


def test_untagged_turn_token_is_dropped():
    """提点/澄清等 turn 节点内无 agent tag 的调用 token 不当发言 delta。"""
    mode, payload = _messages('{"points": ["要点1"]}', node="turn", tags=[])
    assert _to_roundtable_event(mode, payload) is None


def test_non_turn_node_token_is_dropped():
    mode, payload = _messages("调度推理", node="schedule", tags=["agent:ada1"])
    assert _to_roundtable_event(mode, payload) is None


def test_turn_update_event_carries_authoritative_text():
    payload = {
        "turn": {
            "history": [type("M", (), {"sender_id": "ada1", "sender_kind": "ai", "text": "完整发言", "dimension": "现金流"})()]
        }
    }
    ev = _to_roundtable_event("updates", payload)
    assert ev == {"type": "turn", "contact_id": "ada1", "dimension": "现金流", "text": "完整发言"}


def test_interrupt_payload_passthrough():
    intr = (type("I", (), {"value": {"type": "human_gate", "turns_since_human": 1}})(),)
    ev = _to_roundtable_event("updates", {"__interrupt__": intr})
    assert ev["type"] == "human_gate"
