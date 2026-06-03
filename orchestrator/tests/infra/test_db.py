"""S2.1 判据：Contact/Group/Message 增删查改各一条 + 重开 db 数据仍在。"""

from __future__ import annotations

from sqlmodel import select

from app.db.engine import init_models, make_engine, make_session_factory
from app.db.models import Contact, Group, Message


async def test_crud_roundtrip(tmp_path):
    engine = make_engine(str(tmp_path / "t.sqlite"))
    await init_models(engine)
    Session = make_session_factory(engine)

    # create
    async with Session() as s:
        s.add(Contact(id="c1", name="老陈", title="经济顾问"))
        s.add(Group(id="g1", group_key="plat:room", member_ids=["c1"]))
        s.add(Message(id="m1", group_key="plat:room", sender_id="c1", sender_kind="ai", text="hi"))
        await s.commit()

    # read（含 list[str] 的 JSON 列往返）
    async with Session() as s:
        assert (await s.get(Contact, "c1")).name == "老陈"
        assert (await s.get(Group, "g1")).member_ids == ["c1"]
        msgs = (await s.exec(select(Message).where(Message.group_key == "plat:room"))).all()
        assert len(msgs) == 1 and msgs[0].sender_kind == "ai"

    # update
    async with Session() as s:
        c = await s.get(Contact, "c1")
        c.title = "首席经济顾问"
        s.add(c)
        await s.commit()
    async with Session() as s:
        assert (await s.get(Contact, "c1")).title == "首席经济顾问"

    # delete
    async with Session() as s:
        await s.delete(await s.get(Message, "m1"))
        await s.commit()
    async with Session() as s:
        assert (await s.get(Message, "m1")) is None

    await engine.dispose()


async def test_data_survives_reopen(tmp_path):
    db = str(tmp_path / "t2.sqlite")
    engine = make_engine(db)
    await init_models(engine)
    async with make_session_factory(engine)() as s:
        s.add(Contact(id="c1", name="X"))
        await s.commit()
    await engine.dispose()

    # 重开同一 db 文件
    engine2 = make_engine(db)
    async with make_session_factory(engine2)() as s:
        assert (await s.get(Contact, "c1")).name == "X"
    await engine2.dispose()
