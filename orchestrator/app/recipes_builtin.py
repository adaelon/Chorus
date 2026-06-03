"""S5.4.1d: 三内置配方的声明式 JSON（§6.16 C）——"配方=数据"的兑现。

圆桌/扇出/auto 不再是手写的 `StateGraph` 拓扑，而是图原生 `nodes/edges` 数据，经
`compile_recipe` 直译。`build_*_recipe` 只负责组 deps + 选这里的 JSON。这些 dict 也是
S5.4.2a 配方库的内置 seed、S5.4.3 画布的初始模板。

节点 `use` 是 registry 原语名；`id` 是本图实例名（curate 节点 use=curate_gate）。边 `when`
读 next_decision（router/human 落的路由标签）。三/四张图均过 `validate_recipe`（环上有闸、
needs 可达、必有 else）。
"""

from __future__ import annotations

# 扇出：CLARIFY→FRAME→FANOUT→CURATE(自循环)→SYNTHESIZE
FANOUT: dict = {
    "recipe": "fanout", "version": 1,
    "nodes": [
        {"id": "clarify", "use": "clarify"},
        {"id": "frame", "use": "frame"},
        {"id": "fanout", "use": "fanout"},
        {"id": "curate", "use": "curate_gate"},
        {"id": "synthesize", "use": "synthesize"},
    ],
    "edges": [
        {"from": "START", "to": "clarify"},
        {"from": "clarify", "to": "frame"},
        {"from": "frame", "to": "fanout"},
        {"from": "fanout", "to": "curate"},
        {"from": "curate", "when": {"field": "next_decision", "op": "==", "value": "curate"}, "to": "curate"},
        {"from": "curate", "to": "synthesize"},  # else
        {"from": "synthesize", "to": "END"},
    ],
}

# 圆桌（人在环）：CLARIFY→FRAME→SCHEDULE⇄TURN→HUMAN_GATE→…→SYNTHESIZE
ROUNDTABLE: dict = {
    "recipe": "roundtable", "version": 1,
    "nodes": [
        {"id": "clarify", "use": "clarify"},
        {"id": "frame", "use": "frame"},
        {"id": "schedule", "use": "schedule"},
        {"id": "turn", "use": "turn"},
        {"id": "human_gate", "use": "human_gate"},
        {"id": "synthesize", "use": "synthesize"},
    ],
    "edges": [
        {"from": "START", "to": "clarify"},
        {"from": "clarify", "to": "frame"},
        {"from": "frame", "to": "schedule"},
        {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
        {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "yield_to_human"}, "to": "human_gate"},
        {"from": "schedule", "to": "synthesize"},  # else = stop
        {"from": "turn", "to": "human_gate"},
        {"from": "human_gate", "when": {"field": "next_decision", "op": "==", "value": "end"}, "to": "synthesize"},
        {"from": "human_gate", "to": "schedule"},  # else = continue
        {"from": "synthesize", "to": "END"},
    ],
}

# 圆桌（自动连续，无人在环）：CLARIFY→FRAME→SCHEDULE⇄TURN→…→SYNTHESIZE
ROUNDTABLE_CONTINUOUS: dict = {
    "recipe": "roundtable_continuous", "version": 1,
    "nodes": [
        {"id": "clarify", "use": "clarify"},
        {"id": "frame", "use": "frame"},
        {"id": "schedule", "use": "schedule"},
        {"id": "turn", "use": "turn"},
        {"id": "synthesize", "use": "synthesize"},
    ],
    "edges": [
        {"from": "START", "to": "clarify"},
        {"from": "clarify", "to": "frame"},
        {"from": "frame", "to": "schedule"},
        {"from": "schedule", "when": {"field": "next_decision", "op": "==", "value": "next_speaker"}, "to": "turn"},
        {"from": "schedule", "to": "synthesize"},  # else = stop / yield_to_human 都收尾
        {"from": "turn", "to": "schedule"},
        {"from": "synthesize", "to": "END"},
    ],
}

# auto（L3 主持人逐步组原语）：FRAME→PLAN⇄{FANOUT|TURN}→…→SYNTHESIZE
AUTO: dict = {
    "recipe": "auto", "version": 1,
    "nodes": [
        {"id": "frame", "use": "frame"},
        {"id": "plan", "use": "plan"},
        {"id": "fanout", "use": "fanout"},
        {"id": "turn", "use": "turn"},
        {"id": "synthesize", "use": "synthesize"},
    ],
    "edges": [
        {"from": "START", "to": "frame"},
        {"from": "frame", "to": "plan"},
        {"from": "plan", "when": {"field": "next_decision", "op": "==", "value": "fanout"}, "to": "fanout"},
        {"from": "plan", "when": {"field": "next_decision", "op": "==", "value": "speak"}, "to": "turn"},
        {"from": "plan", "to": "synthesize"},  # else = synthesize / stop
        {"from": "fanout", "to": "plan"},
        {"from": "turn", "to": "plan"},
        {"from": "synthesize", "to": "END"},
    ],
}
