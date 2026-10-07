"""Bounded named protein-food preference repair with explicit protected facts."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_references import (
    protein_food_mask,
    supported_protein_foods,
)


@dataclass(frozen=True)
class ProteinFoodRepair:
    recipes: list[Recipe]
    changed_indices: frozenset[int]
    warnings: list[str]


def repair_protein_food_preferences(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    canonical_food: Callable[[str], str],
    food_matches: Callable[[Recipe, str], bool],
    goal_scores: Mapping[str, tuple[int, ...]],
    order: Mapping[str, int],
    replace_slot: int | None = None,
    allow_repair: bool = True,
    source_food_tradeoff: Callable[[Recipe, Recipe], bool] | None = None,
) -> ProteinFoodRepair:
    """Prefer declared/named protein dishes without inventing a hard quota.

    Already hard-screened finished candidates only. Each swap strictly adds
    menu-level named references; existing ingredients, named references, source
    flavors/scenes, exact roles/soups, composition, meal fit, explicit methods
    and every soft goal are retained, except an explicit caller-authorized
    source-backed heart/BP sodium-presence tradeoff. Other goals and appended
    axes still cannot decrease, and the tradeoff must be disclosed. A missing
    score is not authority. Local
    replacement and no-recheck continuation never gain whole-menu permission.
    Unknown or unsupported preferences keep the original ingredient semantics.
    This is a finite reference, not an ingredient ratio/global optimum.
    """
    requested = supported_protein_foods(
        canonical_food(t) for t in constraints.preferred_ingredients
    )
    working = list(menu)
    if not requested:
        return ProteinFoodRepair(working, frozenset(), [])
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    records = {r.recipe_id: r for r in [*working, *candidates]}
    if any(records[r.recipe_id] != r for r in [*working, *candidates]):
        raise ValueError("One recipe identity cannot refer to different source records")
    named = {key: protein_food_mask(r, requested) for key, r in records.items()}
    foods = tuple(
        dict.fromkeys(t for t in constraints.preferred_ingredients if t.strip())
    )
    ingredients = {
        key: sum(1 << i for i, t in enumerate(foods) if food_matches(r, t))
        for key, r in records.items()
    }
    flavors = {
        key: flavor_coverage(r, constraints.preferences) for key, r in records.items()
    }
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {key: scene_reference_mask(r, scenes) for key, r in records.items()}

    def coverage(items: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        value = 0
        for r in items:
            value |= masks[r.recipe_id]
        return value

    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    changed: set[int] = set()
    tradeoffs: set[int] = set()
    health_width = len(dict.fromkeys(constraints.health_goals))
    protected_axes = [
        i
        for i, goal in enumerate(dict.fromkeys(constraints.health_goals))
        if goal not in {"护心", "降压"}
    ]
    if allow_repair:
        for _ in requested:
            before_named = coverage(working, named)
            if before_named == (1 << len(requested)) - 1:
                break
            before_ingredients = coverage(working, ingredients)
            before_flavors = coverage(working, flavors)
            used = {r.recipe_id for r in working}
            best: tuple[tuple[int, int, int, int, str], int, Recipe] | None = None
            for index in positions:
                old = working[index]
                old_scores = goal_scores.get(old.recipe_id)
                if old_scores is None:
                    continue
                rest = working[:index] + working[index + 1 :]
                rest_named = coverage(rest, named)
                rest_ingredients = coverage(rest, ingredients)
                rest_flavors = coverage(rest, flavors)
                rest_names = {"".join(r.name.split()).casefold() for r in rest}
                for candidate in candidates:
                    new_scores = goal_scores.get(candidate.recipe_id)
                    new_named = rest_named | named[candidate.recipe_id]
                    source_tradeoff_allowed = (
                        source_food_tradeoff is not None
                        and new_scores is not None
                        and len(old_scores) == len(new_scores)
                        and len(new_scores) >= health_width
                        and source_food_tradeoff(old, candidate)
                        and no_goal_regression(
                            tuple(new_scores[i] for i in protected_axes)
                            + new_scores[health_width:],
                            tuple(old_scores[i] for i in protected_axes)
                            + old_scores[health_width:],
                        )
                    )
                    if (
                        candidate.recipe_id in used
                        or "".join(candidate.name.split()).casefold() in rest_names
                        or new_scores is None
                        or len(new_scores) < len(set(constraints.health_goals))
                        or not no_goal_regression(new_scores, old_scores)
                        and not source_tradeoff_allowed
                        or set(candidate.categories) != set(old.categories)
                        or meal_cost(candidate, constraints.meal_type)
                        > meal_cost(old, constraints.meal_type)
                        or new_named == before_named
                        or new_named & before_named != before_named
                        or (rest_ingredients | ingredients[candidate.recipe_id])
                        & before_ingredients
                        != before_ingredients
                        or (rest_flavors | flavors[candidate.recipe_id])
                        & before_flavors
                        != before_flavors
                        or scene_masks[candidate.recipe_id] & scene_masks[old.recipe_id]
                        != scene_masks[old.recipe_id]
                        or not composition_satisfied(
                            [*rest, candidate], constraints, previous=working
                        )
                        or not method_swap_preserves(
                            working,
                            index,
                            candidate,
                            constraints.preferences,
                            scoped=constraints.scoped_methods,
                        )
                    ):
                        continue
                    rank = (
                        -(new_named & ~before_named).bit_count(),
                        int(index not in changed),
                        order.get(candidate.recipe_id, len(order)),
                        index,
                        candidate.recipe_id,
                    )
                    if best is None or rank < best[0]:
                        best = (rank, index, candidate)
            if best is None:
                break
            _, index, candidate = best
            if not no_goal_regression(
                goal_scores[candidate.recipe_id], goal_scores[working[index].recipe_id]
            ):
                tradeoffs.add(index)
            working[index] = candidate
            changed.add(index)
    final = coverage(working, named)
    missing = [food for index, food in enumerate(requested) if not final & (1 << index)]
    warnings = [
        "蛋白菜名称参考仅核对原菜名、已声明食材和成菜角色，不测量食材比例、每人份量或营养含量；食材存在不等于该食材命名的蛋白菜。"
    ]
    if tradeoffs:
        warnings.append(
            "为优先有完整原方主体依据的明确鱼/豆腐蛋白菜偏好，"
            "保留原配料并允许护心/降压的内部含钠来源参考分取舍；"
            "不让少量辅料健康加分挡住明确食物需求，不判定原菜或新菜健康优劣。"
            "新方可能含盐或生抽，需核用量、品牌、份数和全天摄入；"
            "其他目标、点名做法、已覆盖食材/口味/场景及本轮权限仍保护，"
            "不宣称低钠、护心功效、每人份量或营养达标。"
        )
    if missing:
        boundary = (
            "本轮保留已接受菜单，不重新调整。"
            if not allow_repair
            else (
                f"本轮只调整第{replace_slot}道，其余菜位保持。"
                if replace_slot is not None
                else "当前同角色候选及已知餐次、目标、口味、场景和明确做法保护下未补齐；不是全库无解证明。"
            )
        )
        warnings.append(
            "尚未覆盖有实际食材依据的蛋白菜名称参考："
            + "、".join(missing)
            + "；仅在配料、主食或汤中出现也不作该参考。"
            + boundary
        )
    return ProteinFoodRepair(working, frozenset(changed), warnings)
