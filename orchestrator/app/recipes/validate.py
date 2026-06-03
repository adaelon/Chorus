"""S5.4.1c: 配方编译期校验（§6.16 A.4/C）——`validate_recipe(json)->list[str]`。

收集**所有**人话错误（空列表=合法），供运行前拦截 + 画布（S5.4.3）实时标红复用。四关：
  ① needs 可达：每节点 spec.needs 在**所有**从 START 到它的路径上被上游 writes（或初始输入）覆盖
     —— must 数据流定点（⊓=∩），处理环。
  ② 必有 else：有 when 出边的节点必须恰有一条无 when 的兜底边；无条件出边不得 >1（不支持并行/歧义）。
  ③ 环上有闸：去掉所有"闸"节点（带 budget 的 router / human 节点）后，余图必须无环
     （否则存在无闸的自主环 → 可能死循环；human 节点靠 interrupt 暂停等人，亦视作闸）。
  ④ when 合法：每条边的 when 经 check_cond 静态校验（字段/算子白名单）。
另含结构前置：节点 id 唯一、use 已注册、边端点已知、START 有出边、END 可达、节点从 START 可达。

初始输入契约 INITIAL = 运行时必给的字段（group_key/roster/history）——needs 以此为起点。
"""

from __future__ import annotations

from collections import OrderedDict
from functools import reduce

from .cond import STATE_FIELDS, check_cond
from .spec import REGISTRY, Primitive

INITIAL_FIELDS = frozenset({"group_key", "roster", "history"})


def _intersect_all(sets: list) -> set:
    """多集合交（容忍 set/frozenset 混用）；空 → 空集。"""
    return set(reduce(lambda a, b: a & b, sets)) if sets else set()


def validate_recipe(recipe: dict, *, registry: dict[str, Primitive] = REGISTRY) -> list[str]:
    """校验配方 JSON，返回人话错误列表（空=合法）。"""
    errs: list[str] = []
    nodes = recipe.get("nodes", [])
    edges = recipe.get("edges", [])

    # ---- 结构前置 ----
    ids: list[str] = []
    use_of: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id")
        if nid in ids:
            errs.append(f"节点 id 重复：{nid!r}")
        ids.append(nid)
        use = n.get("use")
        if use not in registry:
            errs.append(f"节点 {nid!r} 引用了未注册原语 {use!r}")
        else:
            use_of[nid] = use
    idset = set(ids)
    endpoints = idset | {"START", "END"}

    for e in edges:
        if e.get("from") not in endpoints:
            errs.append(f"边引用了未知节点（from）：{e.get('from')!r}")
        if e.get("to") not in endpoints:
            errs.append(f"边引用了未知节点（to）：{e.get('to')!r}")
        if "when" in e:
            try:
                check_cond(e["when"])
            except ValueError as ex:
                errs.append(f"边 {e.get('from')}→{e.get('to')} 条件非法：{ex}")

    # 致命结构错（端点未知会让后续图分析失真）→ 先返回
    if any("未知节点" in m or "未注册原语" in m for m in errs):
        return errs

    # 按 from 归组
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for e in edges:
        groups.setdefault(e["from"], []).append(e)

    # ---- ② 必有 else / 无条件出边唯一 ----
    for src, es in groups.items():
        if src == "END":
            continue
        when_edges = [e for e in es if "when" in e]
        else_edges = [e for e in es if "when" not in e]
        if when_edges and not else_edges:
            errs.append(f"节点 {src!r} 有条件出边但缺 else（无 when 的兜底边）")
        if len(else_edges) > 1:
            errs.append(f"节点 {src!r} 有多条无条件出边（else 须唯一，不支持并行/歧义）")

    # ---- 可达性（BFS from START）----
    succ: dict[str, list[str]] = {}
    for e in edges:
        succ.setdefault(e["from"], []).append(e["to"])
    if not succ.get("START"):
        errs.append("START 没有出边")
    reachable: set[str] = set()
    stack = ["START"]
    while stack:
        u = stack.pop()
        for v in succ.get(u, []):
            if v not in reachable:
                reachable.add(v)
                stack.append(v)
    for nid in ids:
        if nid not in reachable:
            errs.append(f"节点 {nid!r} 从 START 不可达")
    if "END" not in reachable:
        errs.append("END 从 START 不可达（图无终点）")

    # ---- ① needs 可达（must 数据流定点）----
    preds: dict[str, list[str]] = {nid: [] for nid in ids}
    for e in edges:
        if e["to"] in idset:
            preds[e["to"]].append(e["from"])

    def writes_of(nid: str) -> set[str]:
        return set(registry[use_of[nid]].spec.writes)

    universe = set(STATE_FIELDS)
    out: dict[str, set[str]] = {nid: set(universe) for nid in ids}  # top
    changed = True
    while changed:
        changed = False
        for nid in ids:
            ins = [INITIAL_FIELDS if p == "START" else out[p] for p in preds[nid]]
            new_in = _intersect_all(ins)
            new_out = new_in | writes_of(nid)
            if new_out != out[nid]:
                out[nid] = new_out
                changed = True
    for nid in ids:
        if nid not in reachable:
            continue  # 不可达另有报错，不重复
        ins = [INITIAL_FIELDS if p == "START" else out[p] for p in preds[nid]]
        avail = _intersect_all(ins)
        missing = set(registry[use_of[nid]].spec.needs) - avail
        if missing:
            errs.append(f"节点 {nid!r} 的前置 {sorted(missing)} 未被上游写入（needs 不满足）")

    # ---- ③ 环上有闸：去掉所有"闸"节点后须无环 ----
    # 闸 = 带 budget 的 router（预算停）或 human 节点（interrupt 暂停等人，不会自主空转）。
    def _is_gate(nid: str) -> bool:
        spec = registry[use_of[nid]].spec
        return spec.budget is not None or spec.kind == "human"

    adj = {nid: [] for nid in ids if not _is_gate(nid)}
    for e in edges:
        if e["from"] in adj and e["to"] in adj:
            adj[e["from"]].append(e["to"])
    cycle = _find_cycle(adj)
    if cycle is not None:
        errs.append(f"存在无闸的环：{' → '.join(cycle)}（每个环须含一个带 budget 的 router 或 human 节点）")

    return errs


def _find_cycle(adj: dict[str, list[str]]) -> list[str] | None:
    """DFS 找一个环（back-edge），返回环上节点序列；无环 → None。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in adj}
    stack: list[str] = []

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        stack.append(u)
        for v in adj[u]:
            if v not in color:
                continue
            if color[v] == GRAY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                got = dfs(v)
                if got is not None:
                    return got
        stack.pop()
        color[u] = BLACK
        return None

    for n in adj:
        if color[n] == WHITE:
            got = dfs(n)
            if got is not None:
                return got
    return None
