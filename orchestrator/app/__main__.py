"""最小'服务进程'：建空图、对本地 sqlite 跑一次 invoke、打印持久化的 state。

S1.1 阶段还没有 HTTP API（见 S1.6）。这里只验证骨架可运行：
    C:\\Python314\\... -m app
"""

from __future__ import annotations

from pathlib import Path

from .graph import build_app, make_checkpointer


def main() -> None:
    db = Path("group_checkpoints.sqlite")
    cp = make_checkpointer(db)
    app = build_app(cp)
    cfg = {"configurable": {"thread_id": "demo-group"}}

    out = app.invoke({"group_key": "demo-group"}, cfg)
    print("invoked. persisted state:", out)

    snap = app.get_state(cfg)
    print("reloaded state:", snap.values)
    cp.conn.close()


if __name__ == "__main__":
    main()
