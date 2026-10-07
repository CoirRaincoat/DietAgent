"""Atomic bounded multi-slot editing using existing feasibility and ranking."""
from app.agent.planner import MenuPlanner, PlanResult, ScopedCandidateUnavailable
from app.domain.dish_composition import composition_satisfied
from app.domain.meal_roles import dessert_structure_satisfied, is_menu_recipe
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import missing_scoped_methods
from app.domain.slot_food_exclusions import scoped_food_menu_issue, slot_food_allowed
from app.rules.engine import compact


def plan_batch_edit(planner: MenuPlanner, candidates: list[Recipe], constraints: Constraints,
                    current: list[Recipe], slots: list[int], reject_ids: set[str] | None,
                    query_terms: list[str] | None, recheck: bool = True) -> PlanResult:
    mutable = set(slots)
    if (not mutable or len(slots) != len(mutable) or len(current) != constraints.dish_count
            or any(not 1 <= slot <= len(current) for slot in mutable)):
        return PlanResult(failure="多菜位调整需沿当前完整菜单且总菜数不变；请核对保留和替换菜位。")
    for index, recipe in enumerate(current, 1):
        if index not in mutable and (
            not planner.rules.evaluate(recipe, constraints).allowed
            or not is_menu_recipe(recipe, constraints)
            or not slot_food_allowed(recipe, index, constraints, planner.rules)
            or recipe.recipe_id in (reject_ids or ())
        ):
            return PlanResult(failure=f"要保留的第{index}道不满足当前安全或明确限制；未强行保留、未擅自换掉，请确认其他调整范围。")
    rejected = set(reject_ids or ())
    rejected.update(current[slot - 1].recipe_id for slot in mutable)
    try:
        result = planner.plan(candidates, constraints, current=current,
                              reject_ids=reject_ids, query_terms=query_terms,
                              recheck_soft_preferences=False, _edit_slots=mutable)
    except ScopedCandidateUnavailable as error:
        return PlanResult(failure=str(error))
    if result.failure:
        return result
    chosen = result.recipes
    if len(chosen) != len(current) or any(old != new for i, (old, new) in enumerate(zip(current, chosen), 1)
                                       if i not in mutable):
        return PlanResult(failure="当前限制需要修改保留菜位，多菜位调整未提交；请确认是否允许扩大范围。")
    # Existing local repairs are reused at most once per target, against a
    # complete legal scaffold. No intermediate menu is exposed or persisted.
    warnings = []
    if recheck:
        for slot in sorted(mutable):
            pool = [r for r in candidates if slot_food_allowed(r, slot, constraints, planner.rules)]
            try:
                refined = planner.plan(pool, constraints, current=chosen, replace_slot=slot,
                                       reject_ids=rejected, query_terms=query_terms)
            except ScopedCandidateUnavailable:
                continue
            if (not refined.failure and len(refined.recipes) == len(chosen)
                    and all(old == new for i, (old, new) in enumerate(zip(chosen, refined.recipes), 1)
                            if i != slot)
                    and not scoped_food_menu_issue(refined.recipes, constraints, planner.rules)):
                chosen = refined.recipes
                warnings.extend(refined.warnings)
    rejected_names = {compact(current[s - 1].name) for s in mutable}
    if (len({r.recipe_id for r in chosen}) != len(chosen)
            or sum("soup" in r.categories for r in chosen) != constraints.soup_count
            or not composition_satisfied(chosen, constraints)
            or not dessert_structure_satisfied(chosen, constraints)
            or missing_scoped_methods(chosen, constraints.scoped_methods, required_only=True)
            or scoped_food_menu_issue(chosen, constraints, planner.rules)
            or any(not planner.rules.evaluate(r, constraints).allowed for r in chosen)
            or any(not is_menu_recipe(r, constraints) for r in chosen)
            or any(compact(chosen[s - 1].name) in rejected_names for s in mutable)):
        return PlanResult(failure="多菜位结果未通过完整菜单限制核验，本轮没有提交部分换菜；请核对菜位和要求。")
    _, _, gaps = planner._cover_ingredient_preferences(chosen, {}, {}, constraints, chosen, None, {}, allow_repair=False)
    # Intermediate-stage gap disclosures are not final facts.
    warnings = [w for w in warnings if not w.startswith("当前菜单未覆盖食材偏好：")]
    return PlanResult(recipes=chosen, warnings=list(dict.fromkeys([*warnings, *gaps])), changes=[
        {"slot": s, "old_recipe_id": current[s - 1].recipe_id, "new_recipe_id": chosen[s - 1].recipe_id,
         "reason": "仅调整明确授权的菜位，其他完整原方保持不变"} for s in sorted(mutable)])
