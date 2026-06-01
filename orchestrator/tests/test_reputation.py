"""S2.3 判据：eliminate 降信誉 / Contact 仍可被邀 / pick 可回升（可逆，非处决）。"""

from __future__ import annotations

from app.db.engine import init_models, make_engine, make_session_factory
from app.db.models import Contact
from app.db.repo import reputation_adjuster_from
from app.nodes.curate import Eliminate, Pick, curate
from app.state import AgentSlot, Candidate, GroupState, Msg


async def _fake_gen(slot, request, history):
    return Candidate(contact_id=slot.contact_id, dimension=slot.dimension, text="x")


def _state():
    return GroupState(
        group_key="g",
        roster=[AgentSlot(contact_id="A"), AgentSlot(contact_id="B")],
        candidates=[Candidate(contact_id="A", text="a"), Candidate(contact_id="B", text="b")],
        pending_human=Msg(sender_id="u", sender_kind="human", text="q"),
    )


async def test_curate_calls_adjuster_on_eliminate_and_pick():
    calls = []

    async def spy(cid, delta):
        calls.append((cid, delta))

    await curate(
        _state(),
        [Eliminate(contact_id="A"), Pick(contact_id="B")],
        generate=_fake_gen,
        reputation_adjuster=spy,
    )
    assert ("A", -1.0) in calls  # eliminate 降
    assert ("B", 1.0) in calls  # pick 升


async def test_reputation_reversible_and_invitable(tmp_path):
    engine = make_engine(str(tmp_path / "rep.sqlite"))
    await init_models(engine)
    Session = make_session_factory(engine)
    async with Session() as s:
        s.add(Contact(id="A", name="老陈"))
        await s.commit()

    adjust = reputation_adjuster_from(Session)

    # eliminate 信号 → 信誉降，但 Contact 仍在注册表（可被邀，非处决）
    await adjust("A", -1.0)
    async with Session() as s:
        c = await s.get(Contact, "A")
        assert c is not None and c.reputation == -1.0

    # 可逆：pick 信号回升
    await adjust("A", 1.0)
    async with Session() as s:
        assert (await s.get(Contact, "A")).reputation == 0.0

    await engine.dispose()
