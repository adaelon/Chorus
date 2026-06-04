"""S5.0: transport 无关的会话运行时——把圆桌图的输出统一成中性 OutboundEvent。

§6.12：web 与 telegram 原本各写一套"图输出→自己的事件"映射（`_to_roundtable_event`
vs `RelayDriver._push_new`），重复。这里抽成**一份**：

  to_event(mode, payload)         —— 纯映射：astream 的一个 (mode,payload) → 一个中性事件
  iter_events(graph, input, cfg)  —— 跑 graph.astream，吐中性事件流

中性事件（transport 无关）：
  Delta(token 级，按 agent 路由)/ Framed(分维度)/ TurnDone(一轮发言完成)/
  Output(主笔综合)/ Interrupt(human_gate|clarify 暂停，payload 自带 type)。

各 adapter 决定**节奏**：web 每次 HTTP 跑一段 astream 并 SSE 出去；telegram 后台循环
跑到 END。两端共用同一份事件语义；status/done 等传输细节由各 adapter 自加。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Delta:
    contact_id: str
    text: str

    def to_dict(self) -> dict:
        return {"type": "delta", "contact_id": self.contact_id, "text": self.text}


@dataclass
class Framed:
    roster: list[dict]  # [{contact_id, dimension}]

    def to_dict(self) -> dict:
        return {"type": "framed", "roster": self.roster}


@dataclass
class TurnDone:
    contact_id: str
    dimension: str | None
    text: str

    def to_dict(self) -> dict:
        return {
            "type": "turn",
            "contact_id": self.contact_id,
            "dimension": self.dimension,
            "text": self.text,
        }


@dataclass
class Output:
    text: str

    def to_dict(self) -> dict:
        return {"type": "output", "output": self.text}


@dataclass
class Interrupt:
    payload: dict  # human_gate / clarify —— 自带 "type"

    def to_dict(self) -> dict:
        return dict(self.payload)


OutboundEvent = Delta | Framed | TurnDone | Output | Interrupt


def _agent_id(meta: dict) -> str | None:
    for t in meta.get("tags") or []:
        if t.startswith("agent:"):
            return t.split(":", 1)[1]
    return None


def to_event(mode: str, payload) -> OutboundEvent | None:
    """纯映射：astream 的一个 (mode,payload) → 一个中性事件；不关心的返回 None。"""
    if mode == "messages":
        chunk, meta = payload
        content = getattr(chunk, "content", "")
        if not content or meta.get("langgraph_node") != "turn":
            return None
        cid = _agent_id(meta)
        if not cid:
            return None  # 无 agent tag = turn 节点内的非发言调用（提点等），不当 delta
        return Delta(cid, content if isinstance(content, str) else str(content))
    if mode == "updates":
        payload = payload or {}
        intr = payload.get("__interrupt__")
        if intr:
            return Interrupt(dict(intr[0].value))  # human_gate / clarify
        for node, delta in payload.items():
            if not delta:
                continue
            if node == "frame" and "roster" in delta:
                return Framed(
                    [
                        {"contact_id": s.contact_id, "dimension": s.dimension}
                        for s in delta["roster"]
                    ]
                )
            if node == "turn" and delta.get("history"):
                last = delta["history"][-1]
                return TurnDone(last.sender_id, last.dimension, last.text)
            if node in ("synthesize", "produce") and "output" in delta:
                return Output(delta["output"])  # S10b：produce 也是终端产出（出产物）
    return None


async def iter_events(graph, stream_input, cfg):
    """跑 graph.astream（updates+messages），吐中性 OutboundEvent 流。

    stream_input 可是初始 state（起场）或 `Command(resume=...)`（续场）；图在 interrupt
    处自然结束本段 astream（此时 `aget_state().next` 非空 = 暂停待续）。
    """
    async for mode, payload in graph.astream(
        stream_input, cfg, stream_mode=["updates", "messages"]
    ):
        ev = to_event(mode, payload)
        if ev is not None:
            yield ev
