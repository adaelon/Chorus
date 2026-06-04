"""S5.0 回归：runtime.to_event（中性事件映射，原 _to_roundtable_event）。

只把**带 agent tag** 的 turn-node token 当 delta（turn 节点内的提点 extractor token
无 tag，不能当发言气泡——实测 telegram/web 都踩过）。其余映射 framed/turn/interrupt。
"""

from __future__ import annotations

from app.transport.runtime import Delta, Framed, Interrupt, TurnDone, to_event


class _Chunk:
    def __init__(self, content: str):
        self.content = content


def _messages(content: str, *, node: str, tags=None):
    return ("messages", (_Chunk(content), {"langgraph_node": node, "tags": tags or []}))


def test_agent_tagged_turn_token_is_delta():
    ev = to_event(*_messages("半句话", node="turn", tags=["agent:ada1"]))
    assert isinstance(ev, Delta)
    assert ev.to_dict() == {"type": "delta", "contact_id": "ada1", "text": "半句话"}


def test_untagged_turn_token_is_dropped():
    """提点/澄清等 turn 节点内无 agent tag 的调用 token 不当发言 delta。"""
    assert to_event(*_messages('{"points": ["要点1"]}', node="turn", tags=[])) is None


def test_non_turn_node_token_is_dropped():
    assert to_event(*_messages("调度推理", node="schedule", tags=["agent:ada1"])) is None


def test_turn_update_event_carries_authoritative_text():
    payload = {
        "turn": {
            "history": [
                type(
                    "M",
                    (),
                    {"sender_id": "ada1", "sender_kind": "ai", "text": "完整发言", "dimension": "现金流"},
                )()
            ]
        }
    }
    ev = to_event("updates", payload)
    assert isinstance(ev, TurnDone)
    assert ev.to_dict() == {
        "type": "turn",
        "contact_id": "ada1",
        "dimension": "现金流",
        "text": "完整发言",
    }


def test_frame_update_is_framed():
    payload = {"frame": {"roster": [type("S", (), {"contact_id": "A", "dimension": "d"})()]}}
    ev = to_event("updates", payload)
    assert isinstance(ev, Framed)
    assert ev.to_dict() == {"type": "framed", "roster": [{"contact_id": "A", "dimension": "d"}]}


def test_interrupt_payload_passthrough():
    intr = (type("I", (), {"value": {"type": "human_gate", "turns_since_human": 1}})(),)
    ev = to_event("updates", {"__interrupt__": intr})
    assert isinstance(ev, Interrupt)
    assert ev.to_dict()["type"] == "human_gate"


def test_deliver_interrupt_passthrough():
    """S10b：deliver 选择闸的 interrupt 原样透传 type=deliver（前端据此渲染两按钮）。"""
    intr = (type("I", (), {"value": {"type": "deliver", "options": ["decide", "produce"]}})(),)
    ev = to_event("updates", {"__interrupt__": intr})
    assert isinstance(ev, Interrupt)
    assert ev.to_dict()["type"] == "deliver"


def test_synthesize_and_produce_outputs_are_output_events():
    """S10b：synthesize（出结论）与 produce（出产物）都是终端产出 → Output 事件。"""
    from app.transport.runtime import Output

    syn = to_event("updates", {"synthesize": {"output": "结论"}})
    prod = to_event("updates", {"produce": {"output": "产物"}})
    assert isinstance(syn, Output) and syn.to_dict() == {"type": "output", "output": "结论"}
    assert isinstance(prod, Output) and prod.to_dict() == {"type": "output", "output": "产物"}
