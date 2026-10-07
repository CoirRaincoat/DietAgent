"""Bounded source-flavor coverage repair inside verified meal/edit boundaries."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import (
    flavor_coverage,
    flavor_preference_issues,
    flavor_strength,
    scene_reference_mask,
    supported_flavor_preferences,
)
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe


@dataclass(frozen=True)
class FlavorRepair:
    """Repaired source records and zero-based changed positions, not a grade."""

    recipes: list[Recipe]
    changed_positions: frozenset[int]
    warnings: list[str]


def repair_flavor_preferences(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    goal_scores: Mapping[str, tuple[int, ...]],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> FlavorRepair:
    """Add covered positive flavors without sacrificing already checked facts.

    The caller supplies only hard-screened, finished-meal, name-deduplicated
    candidates and complete per-goal vectors. No safety checks or source data
    are substituted here. Every accepted same-role swap strictly increases
    the flavor bitmask, preserves covered ingredients and flavors, explicit
    meat/no-meat quotas, source meal fit, and each configured goal separately.
    Missing goal vectors cannot authorize a swap, even with no requested goals.

    At most one new requested flavor is required per accepted iteration, so
    len(requested flavors) bounds the loop. A local edit protects other slots;
    allow_repair=False reports gaps without changing an accepted menu. Unknown
    flavor descriptions and mere ingredient cues do not create coverage.
    """
    working = list(menu)
    preferences = supported_flavor_preferences(constraints.preferences)
    issues = list(flavor_preference_issues(constraints.preferences,
        excluded_ingredients=constraints.excluded_ingredients))
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    if not preferences:
        return FlavorRepair(working, frozenset(), issues)

    records: dict[str, Recipe] = {}
    for record in [*working, *candidates]:
        if record.recipe_id in records and records[record.recipe_id] != record:
            raise ValueError("One recipe identity cannot refer to different source records")
        records[record.recipe_id] = record
    flavor_masks = {key: flavor_coverage(record, preferences) for key, record in records.items()}
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {
        key: scene_reference_mask(record, scenes) if scenes else 0
        for key, record in records.items()
    }
    ingredients = tuple(
        dict.fromkeys(term for term in constraints.preferred_ingredients if term.strip())
    )
    ingredient_masks = {
        key: sum(1 << i for i, term in enumerate(ingredients) if food_matches(record, term))
        for key, record in records.items()
    }

    def coverage(records_to_cover: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        mask = 0
        for record in records_to_cover:
            mask |= masks[record.recipe_id]
        return mask

    def name_key(record: Recipe) -> str:
        return re.sub(r"\s+", "", record.name).casefold()

    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    required_goals = len(dict.fromkeys(constraints.health_goals))
    changed: set[int] = set()
    if allow_repair:
        for _ in preferences:
            covered_flavors = coverage(working, flavor_masks)
            if covered_flavors == (1 << len(preferences)) - 1:
                break
            covered_ingredients = coverage(working, ingredient_masks)
            used = {record.recipe_id for record in working}
            best: tuple[tuple[int, int, int, int, int, str], int, Recipe] | None = None
            for index in positions:
                previous = working[index]
                previous_scores = goal_scores.get(previous.recipe_id)
                if previous_scores is None or len(previous_scores) < required_goals:
                    continue
                rest = working[:index] + working[index + 1 :]
                rest_flavors = coverage(rest, flavor_masks)
                rest_ingredients = coverage(rest, ingredient_masks)
                rest_names = {name_key(record) for record in rest}
                for candidate in candidates:
                    new_scores = goal_scores.get(candidate.recipe_id)
                    new_flavors = rest_flavors | flavor_masks[candidate.recipe_id]
                    new_ingredients = rest_ingredients | ingredient_masks[candidate.recipe_id]
                    if (
                        candidate.recipe_id in used
                        or name_key(candidate) in rest_names
                        or new_scores is None
                        or len(new_scores) < required_goals
                        or not no_goal_regression(new_scores, previous_scores)
                        or set(candidate.categories) != set(previous.categories)
                        or scene_masks[candidate.recipe_id] & scene_masks[previous.recipe_id]
                        != scene_masks[previous.recipe_id]
                        or meal_cost(candidate, constraints.meal_type)
                        > meal_cost(previous, constraints.meal_type)
                        or new_flavors == covered_flavors
                        or new_flavors & covered_flavors != covered_flavors
                        or new_ingredients & covered_ingredients != covered_ingredients
                        or not composition_satisfied([*rest, candidate], constraints, previous=working)
                        or not method_swap_preserves(working, index, candidate, constraints.preferences, scoped=constraints.scoped_methods)
                    ):
                        continue
                    added = new_flavors & ~covered_flavors
                    strength = sum(
                        flavor_strength(candidate, preference)
                        for i, preference in enumerate(preferences)
                        if added & (1 << i)
                    )
                    rank = (
                        -added.bit_count(),
                        int(index not in changed),
                        -strength,
                        order.get(candidate.recipe_id, len(order)),
                        index,
                        candidate.recipe_id,
                    )
                    if best is None or rank < best[0]:
                        best = (rank, index, candidate)
            if best is None:
                break
            _, index, candidate = best
            working[index] = candidate
            changed.add(index)

    final_mask = coverage(working, flavor_masks)
    missing = [preference for i, preference in enumerate(preferences) if not final_mask & (1 << i)]
    warnings = [
        "口味匹配仅采用原始口味标签或菜名直接参考，不是实际尝味、清淡用量、低钠、健康或不辣保证；"
        "仅有食材线索的口味保持未知，不作为已满足偏好。"
    ]
    if missing:
        boundary = (
            "本轮不执行口味换菜，保留已接受的菜单和菜位。"
            if not allow_repair
            else (
                f"本轮仅允许调整第 {replace_slot} 道菜，其余菜位不变。"
                if replace_slot is not None
                else "在当前安全候选、同成菜角色、已覆盖偏好、荤素数量、餐次和逐目标定性排序保护下仍未补齐。"
            )
        )
        warnings.append(
            "当前菜单未覆盖有明确来源参考的口味偏好：" + "、".join(missing) + "；" + boundary
        )
    return FlavorRepair(working, frozenset(changed), [*warnings, *issues])
