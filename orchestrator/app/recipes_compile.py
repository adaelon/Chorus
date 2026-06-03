"""S5.4.1b: 声明式 DAG → LangGraph 编译器（§6.16 C）——`compile_recipe(json)->StateGraph`。

把图原生 JSON 直译成 `StateGraph`，无智能：
  nodes[{id,use,args}] → registry[use].node 经 inspect 过滤注入 deps（+据 spec.budget 插闸）→ add_node
  edges[{from,to,when?}]：
    - 某源仅一条无 when 出边 → add_edge（transform 普通边）
    - 否则按 from 归组 → add_conditional_edges：顺序 eval_cond，命中其 to；无 when 的边作 else 兜底
  "START"/"END" 字符串 → LangGraph 常量。

deps 是注入的原语依赖（assign/generate/persona_provider/extract/pick/planner/compose/
reputation_adjuster/assess…），按节点函数的形参名过滤——节点缺谁就不传谁（走其默认）。
**args 暂不处理**（当前 spec.args 全 None；含 state 字段的覆盖在 run 时落初始 state，S5.4.2b）。
校验（needs 可达/必有 else/环上有闸）= S5.4.1c；本刀只直译。
"""

from __future__ import annotations

import inspect
from collections import OrderedDict
from typing import Any, Callable

from functools import partial

from langgraph.graph import END, START, StateGraph

from .recipes_cond import eval_cond
from .recipes_spec import REGISTRY, Primitive
from .state import GroupState

_TERMINALS = {"START": START, "END": END}


def _node_ref(name: str):
    """JSON 里的 "START"/"END" → LangGraph 常量；其余原样（节点 id）。"""
    return _TERMINALS.get(name, name)


def _bind(prim: Primitive, deps: dict[str, Any]) -> Callable:
    """按节点形参名过滤注入 deps；router 据 spec.budget 自动插闸（§6.16 A.4）。"""
    params = inspect.signature(prim.node).parameters
    kw = {k: v for k, v in deps.items() if k in params}
    if prim.spec.budget is not None and "budget" in params and "budget" not in kw:
        kw["budget"] = prim.spec.budget
    return partial(prim.node, **kw) if kw else prim.node


def _make_router(edges: list[dict]) -> Callable[[GroupState], str]:
    """条件边路由：顺序 eval_cond，命中返回其 to（path_map 的键）；无 when 边作 else。"""

    def route(state: GroupState) -> str:
        for e in edges:
            when = e.get("when")
            if when is None or eval_cond(when, state):
                return e["to"]
        raise ValueError("条件边无匹配分支且无 else 兜底")  # 1c 编译期防住；运行期保险

    return route


def compile_recipe(
    recipe: dict,
    checkpointer=None,
    *,
    deps: dict[str, Any] | None = None,
    registry: dict[str, Primitive] = REGISTRY,
):
    """声明式配方 JSON → 编译好的 StateGraph（直译，§6.16 C）。"""
    deps = deps or {}
    g = StateGraph(GroupState)

    for n in recipe["nodes"]:
        use = n["use"]
        if use not in registry:
            raise ValueError(f"节点 {n.get('id')!r} 引用了未注册原语 {use!r}")
        g.add_node(n["id"], _bind(registry[use], deps))

    # 按 from 归组，保序（else 边须在该组最后）
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for e in recipe["edges"]:
        groups.setdefault(e["from"], []).append(e)

    for src, es in groups.items():
        if len(es) == 1 and "when" not in es[0]:
            g.add_edge(_node_ref(src), _node_ref(es[0]["to"]))
        else:
            path_map = {e["to"]: _node_ref(e["to"]) for e in es}
            g.add_conditional_edges(_node_ref(src), _make_router(es), path_map)

    return g.compile(checkpointer=checkpointer)
