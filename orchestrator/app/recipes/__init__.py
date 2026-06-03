"""配方子系统（§6.16）：原语 spec/registry、声明式 DAG 编译/校验、内置配方与装配、L2 选配方。

再导出公共 API，外部按 `from ..recipes import X` 用，无需关心内部分文件。
"""

from __future__ import annotations

from .auto import build_auto_recipe
from .builtin import AUTO, FANOUT, ROUNDTABLE, ROUNDTABLE_CONTINUOUS
from .compile import compile_recipe
from .cond import check_cond, eval_cond
from .fanout import build_fanout_recipe
from .plan_recipe import RecipePlan, RecipePlanner, default_recipe_planner, plan_recipe
from .roundtable import build_roundtable_recipe
from .select import (
    DEFAULT_RECIPE,
    RECIPES,
    RecipeChoice,
    RecipeSelector,
    default_recipe_selector,
    select_recipe,
)
from .spec import REGISTRY, Primitive, PrimitiveSpec, check_spec, validate_registry
from .validate import validate_recipe

__all__ = [
    "build_fanout_recipe",
    "build_roundtable_recipe",
    "build_auto_recipe",
    "compile_recipe",
    "validate_recipe",
    "eval_cond",
    "check_cond",
    "REGISTRY",
    "Primitive",
    "PrimitiveSpec",
    "check_spec",
    "validate_registry",
    "FANOUT",
    "ROUNDTABLE",
    "ROUNDTABLE_CONTINUOUS",
    "AUTO",
    "select_recipe",
    "default_recipe_selector",
    "RecipeSelector",
    "RecipeChoice",
    "RECIPES",
    "DEFAULT_RECIPE",
    "plan_recipe",
    "RecipePlan",
    "RecipePlanner",
    "default_recipe_planner",
]
