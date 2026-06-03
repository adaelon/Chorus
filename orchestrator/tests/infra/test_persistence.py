"""S1.1 判据：空图 invoke → state 落 sqlite → 模拟重启 reload 出同一 state。"""

from __future__ import annotations

from app.graph import build_app, make_checkpointer
from app.state import GroupState

CFG = {"configurable": {"thread_id": "g1"}}


def test_state_persists_across_restart(tmp_path):
    db = tmp_path / "chk.sqlite"

    # 第一次进程：invoke 空图，写入 state
    cp1 = make_checkpointer(db)
    app1 = build_app(cp1)
    out = app1.invoke({"group_key": "g1", "max_turns_per_human": 4}, CFG)
    assert out["group_key"] == "g1"
    cp1.conn.close()  # 模拟进程退出

    # 第二次进程：同一 db 新开 checkpointer，reload 出同一 state
    cp2 = make_checkpointer(db)
    app2 = build_app(cp2)
    snap = app2.get_state(CFG)
    cp2.conn.close()

    # 写入的值原样跨"重启"回来
    assert snap.values["group_key"] == "g1"
    assert snap.values["max_turns_per_human"] == 4

    # 持久化的 channel 值能重新水化成完整 GroupState（未写入字段由模型补默认）
    rehydrated = GroupState(**snap.values)
    assert rehydrated.group_key == "g1"
    assert rehydrated.max_turns_per_human == 4
    assert rehydrated.turns_since_human == 0
