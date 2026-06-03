"""S2.2 判据：混合身份注入——基础人设+临场维度两段拼接、同 Contact 不同维度、generator 用 persona。"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.db.models import Contact
from app.nodes.generate import default_generator, persona_messages
from app.state import AgentSlot, Msg


def test_persona_messages_splices_persona_and_dimension():
    p = Contact(id="c", name="老陈", title="经济顾问", persona_style="务实", base_stance="成本优先")
    msgs = persona_messages(p, "现金流风险", [], "便利店要不要关")
    sys = msgs[0].content
    assert "老陈" in sys and "经济顾问" in sys and "务实" in sys and "成本优先" in sys  # 基础人设
    assert "现金流风险" in sys  # 本场维度（两段拼接）
    assert msgs[-1].content == "便利店要不要关"


def test_same_persona_different_dimensions():
    p = Contact(id="c", name="老陈")
    m1 = persona_messages(p, "成本结构", [], "q")
    m2 = persona_messages(p, "现金流", [], "q")
    assert "成本结构" in m1[0].content and "现金流" in m2[0].content  # 维度随场变
    assert "老陈" in m1[0].content and "老陈" in m2[0].content  # 人设稳定


def test_history_injected():
    p = Contact(id="c", name="老陈")
    hist = [Msg(sender_id="u", sender_kind="human", text="开场白要点")]
    msgs = persona_messages(p, None, hist, "q")
    assert any("开场白要点" in m.content for m in msgs)


class _FakeModel:
    def __init__(self):
        self.seen = None

    async def astream(self, messages, config=None):
        self.seen = messages
        yield AIMessageChunk(content="ok")


async def test_default_generator_uses_persona():
    m = _FakeModel()

    async def provider(cid):
        return Contact(id=cid, name="老陈", title="经济顾问")

    gen = default_generator(m, provider)
    c = await gen(AgentSlot(contact_id="c", dimension="成本"), "需求X", [])
    assert c.contact_id == "c" and c.text == "ok"
    assert "老陈" in m.seen[0].content and "成本" in m.seen[0].content  # 用了 persona+维度


async def test_default_generator_falls_back_without_persona():
    m = _FakeModel()

    async def provider(cid):
        return None

    gen = default_generator(m, provider)
    await gen(AgentSlot(contact_id="x"), "需求", [])
    assert "participant x" in m.seen[0].content  # 无 persona → 占位
