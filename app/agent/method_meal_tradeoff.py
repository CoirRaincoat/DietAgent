"""Bounded source-reference tradeoffs, not a proof of global infeasibility."""

import hashlib
import json
from collections.abc import Sequence
from typing import Literal

from app.agent.meal_context import repair_meal_context
from app.agent.method_preferences import repair_method_preferences
from app.domain.meal_context import meal_cost
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.method_preferences import method_reference_mask, supported_method_preferences
from app.domain.models import Constraints, MethodMealTradeoff, Recipe
from app.rules.engine import RuleEngine

OPTIONS = ["优先餐次参考", "优先明确做法参考", "暂不规划"]
VERSION = "bound-source-method-meal-tradeoff-v1"


def literal_priority_choice(message: str) -> Literal["meal", "method", "pause"] | None:
    text = "".join(message.split()).rstrip("。.!！")
    choices: dict[str, Literal["meal", "method", "pause"]] = {
        "优先餐次参考": "meal",
        "优先餐次": "meal",
        "优先明确做法参考": "method",
        "优先明确做法": "method",
        "暂不规划": "pause",
    }
    return choices.get(text)


def priority_binding(constraints: Constraints, catalog: Sequence[Recipe]) -> str:
    # Full source AND derived fields bind the decision; ID alone is insufficient.
    value = {
        "version": VERSION,
        "constraints": constraints.model_dump(mode="json", exclude={"method_meal_priority"}),
        "catalog": [r.model_dump(mode="json") for r in sorted(catalog, key=lambda r: r.recipe_id)],
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def pending_binding(
    constraints: Constraints,
    catalog: Sequence[Recipe],
    menu_ids: Sequence[str],
    action: str,
    replace_slot: int | None,
) -> str:
    value = (priority_binding(constraints, catalog), tuple(menu_ids), action, replace_slot)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def coverage(menu: Sequence[Recipe], constraints: Constraints) -> int:
    result = 0
    for recipe in menu:
        result |= method_reference_mask(recipe, constraints.preferences)
    return result


def find_tradeoff(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    *,
    replace_slot: int | None = None,
) -> tuple[list[Recipe], list[Recipe]] | None:
    if (
        not menu
        or constraints.method_meal_priority is not None
        or not supported_method_preferences(constraints.preferences)
    ):
        return None
    safe_candidates = [
        r
        for r in safe_candidates
        if r.eligible and is_main_meal_recipe(r) and rules.evaluate(r, constraints).allowed
    ]
    scores = {
        r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [*menu, *safe_candidates]
    }
    order = {r.recipe_id: i for i, r in enumerate(safe_candidates)}
    method = repair_method_preferences(
        menu,
        safe_candidates,
        constraints.model_copy(update={"method_meal_priority": "method"}),
        goal_scores=scores,
        order=order,
        food_matches=lambda r, t: bool(rules.preference_matches(r, t)),
        replace_slot=replace_slot,
    ).recipes
    meal = repair_meal_context(
        method,
        safe_candidates,
        constraints.model_copy(update={"method_meal_priority": "meal"}),
        goal_scores=scores,
        scores={},
        order=order,
        food_matches=lambda r, t: bool(rules.preference_matches(r, t)),
        replace_slot=replace_slot,
    ).recipes
    if (
        sum(meal_cost(r, constraints.meal_type) for r in meal)
        >= sum(meal_cost(r, constraints.meal_type) for r in method)
        or coverage(method, constraints) & ~coverage(meal, constraints) == 0
    ):
        return None
    if any(not rules.evaluate(r, constraints).allowed for r in [*method, *meal]):
        return None
    return meal, method


def make_pending(
    meal: Sequence[Recipe],
    method: Sequence[Recipe],
    constraints: Constraints,
    catalog: Sequence[Recipe],
    original_ids: Sequence[str],
    action: Literal["plan", "replace", "reject"],
    slot: int | None,
) -> MethodMealTradeoff:
    prompt = (
        "当前有界同角色核对仍有餐次／明确做法参考取舍，不能同时补齐；这不是全库无解证明，"
        "也不是菜谱实际适配或份量认证。\n"
        "餐次参考优先的候选菜单：" + "、".join(r.name for r in meal) + "。\n"
        "明确做法参考优先的候选菜单：" + "、".join(r.name for r in method) + "。\n"
        "请选择优先餐次参考、优先明确做法参考，或暂不规划。原要求不会删除，"
        "另一项仍会披露缺口；过敏、不辣和原换菜范围不能放宽。"
    )
    return MethodMealTradeoff(
        binding_hash=pending_binding(constraints, catalog, original_ids, action, slot),
        action=action,
        replace_slot=slot,
        original_menu_ids=list(original_ids),
        meal_option_ids=[r.recipe_id for r in meal],
        method_option_ids=[r.recipe_id for r in method],
        prompt=prompt,
    )
