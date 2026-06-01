"""S1.1: LangGraph 服务骨架——一张空图 + SqliteSaver checkpointer。

空图只有一个 no-op 节点，用于验证 state 能 invoke 并持久化、重启可 reload。
不含任何业务节点（FANOUT/FRAME/CURATE/... 见后续切片）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .state import GroupState


def _noop(state: GroupState) -> dict:
    """空节点：不改变 state，仅让图可被 invoke 并触发 checkpoint。"""
    return {}


def make_checkpointer(db_path: str | Path) -> SqliteSaver:
    """打开（或新建）一个基于文件的 SqliteSaver。

    用裸 sqlite3 连接而非 from_conn_string 上下文管理器，方便在测试里
    显式控制连接生命周期（模拟进程重启）。
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def build_app(checkpointer: SqliteSaver):
    """编译空图，挂上 checkpointer。返回可 invoke 的图。"""
    g = StateGraph(GroupState)
    g.add_node("noop", _noop)
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g.compile(checkpointer=checkpointer)
