"""Bounded per-slot scene references inside already verified meal candidates."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.context_exclusions import context_exclusion_disclosure
from app.domain.dining_scenes import scene_request_issues, supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import flavor_coverage, matching_tags, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.scene_references import scene_reference
from app.rules.engine import compact


@dataclass(frozen=True)
class SceneRepair:
    recipes: list[Recipe]
    changed_positions: frozenset[int]
    setup_changed_positions: frozenset[int] = frozenset()


def simpler_bento_setup(old: Recipe, new: Recipe, requested: Sequence[str]) -> bool:
    """One read-source setup contrast, not a general speed/equipment score.

    Unknown setup is not treated as easy. Full-record binding expires this
    reference on any recipe change, including ingredient/method edits.
    """
    if "便当" not in requested:
        return False
    before, after = scene_reference(old), scene_reference(new)
    return bool(before and after and before.setup_reference == "mould_and_piping"
                and after.setup_reference == "main_pot_program")


def scene_warnings(menu: Sequence[Recipe], constraints: Constraints) -> list[str]:
    requested = supported_scene_preferences(constraints.preferences, constraints.people)
    warnings = list(scene_request_issues(constraints.preferences, constraints.people))
    warnings.extend(context_exclusion_disclosure(menu, constraints))
    for index, recipe in enumerate(menu, start=1):
        mask = scene_reference_mask(recipe, requested)
        supplemented = [
            item.tag for item in matching_tags(recipe).scene_references if item.tag in requested
        ]
        if supplemented:
            warnings.append(
                f"第 {index} 道“{recipe.name}”的场景参考为助手补充："
                + "、".join(supplemented)
                + "；依据原配料与成菜形式，非原始标签，未经独立复核。"
            )
        missing = [tag for i, tag in enumerate(requested) if not mask & (1 << i)]
        if missing:
            warnings.append(
                f"第 {index} 道“{recipe.name}”缺少用餐场景来源参考："
                + "、".join(missing)
                + "；未知不等于不适合，当前候选或受保护菜位内未补齐。"
            )
        reference = scene_reference(recipe) if "便当" in requested else None
        if reference and reference.setup_reference == "mould_and_piping":
            warnings.append(f"「{recipe.name}」原做法需裱花袋与烤肠模具；可提前准备不代表无需这些工具。")
        elif reference and reference.setup_reference == "main_pot_program":
            warnings.append(f"「{recipe.name}」沿原主锅程序制作，原步骤不另用烤肠模具或裱花袋；仍需原设备，未核通用锅具替代、总备餐时间或携带保存条件。")
    if requested or warnings:
        warnings.append(
            "用餐场景仅参考原标签、明确菜名及单列的助手补充；部分菜有参考不代表整餐场景适配已验证，"
            "也不证明携带/保存安全、时间、人数份量或健康功效。"
        )
    return warnings


def repair_scene_preferences(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    goal_scores: Mapping[str, tuple[int, ...]],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    protected_food_terms: Sequence[str] = (),
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> SceneRepair:
    """Increase each changed slot's references, preserving prior verified facts.

    Candidates MUST already pass independent hard/finished-meal gates. Unknown
    context is never an exclusion or certification. No extra fields or source
    steps are written. The finite loop is bounded by menu size * requests;
    every swap adds a per-slot bit or improves the finite read-source bento
    setup contrast, never taking an existing reference away. Setup unknown
    cannot supply a simplicity credit or storage/transport guarantee.
    The entire existing menu's food/flavor coverage, explicit composition,
    per-slot role, meal and each goal are protected. Method coverage/diversity
    is protected only when explicitly requested, not inferred from the old menu.
    No guarantee of a feasible/global-optimal menu follows from no local swap.
    """
    working = list(menu)
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    requested = supported_scene_preferences(constraints.preferences, constraints.people)
    if not requested or not allow_repair:
        return SceneRepair(working, frozenset())
    records: dict[str, Recipe] = {}
    for recipe in [*working, *candidates]:
        if recipe.recipe_id in records and records[recipe.recipe_id] != recipe:
            raise ValueError("One recipe identity cannot refer to different source records")
        records[recipe.recipe_id] = recipe
    scene_masks = {key: scene_reference_mask(r, requested) for key, r in records.items()}
    flavor_masks = {key: flavor_coverage(r, constraints.preferences) for key, r in records.items()}
    foods = tuple(dict.fromkeys(t for t in (*constraints.preferred_ingredients, *protected_food_terms) if t.strip()))
    food_masks = {
        key: sum(1 << i for i, term in enumerate(foods) if food_matches(r, term))
        for key, r in records.items()
    }

    def coverage(menu: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        value = 0
        for recipe in menu:
            value |= masks[recipe.recipe_id]
        return value

    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    changed: set[int] = set()
    setup_changed: set[int] = set()
    required_goals = len(dict.fromkeys(constraints.health_goals))
    for _ in range(len(working) * (len(requested) + 1)):
        before_food = coverage(working, food_masks)
        before_flavor = coverage(working, flavor_masks)
        used = {r.recipe_id for r in working}
        best: tuple[tuple[int, int, int, int, int, str], int, Recipe, bool] | None = None
        for index in positions:
            old = working[index]
            previous = goal_scores.get(old.recipe_id)
            if previous is None or len(previous) < required_goals:
                continue
            rest = working[:index] + working[index + 1 :]
            names = {compact(r.name) for r in rest}
            rest_food, rest_flavor = coverage(rest, food_masks), coverage(rest, flavor_masks)
            old_mask = scene_masks[old.recipe_id]
            for candidate in candidates:
                new = goal_scores.get(candidate.recipe_id)
                new_mask = scene_masks[candidate.recipe_id]
                setup_gain = simpler_bento_setup(old, candidate, requested)
                if (
                    candidate.recipe_id in used
                    or compact(candidate.name) in names
                    or new is None
                    or len(new) < required_goals
                    or not no_goal_regression(new, previous)
                    or new_mask == old_mask and not setup_gain
                    or new_mask & old_mask != old_mask
                    or set(candidate.categories) != set(old.categories)
                    or meal_cost(candidate, constraints.meal_type)
                    > meal_cost(old, constraints.meal_type)
                    or (rest_food | food_masks[candidate.recipe_id]) & before_food != before_food
                    or (rest_flavor | flavor_masks[candidate.recipe_id]) & before_flavor
                    != before_flavor
                    or not composition_satisfied([*rest, candidate], constraints, previous=working)
                ):
                    continue
                if not method_swap_preserves(
                    working,
                    index,
                    candidate,
                    constraints.preferences,
                    scoped=constraints.scoped_methods,
                ):
                    continue
                rank = (
                    -(new_mask & ~old_mask).bit_count(),
                    -int(setup_gain),
                    int(index not in changed),
                    order.get(candidate.recipe_id, len(order)),
                    index,
                    candidate.recipe_id,
                )
                if best is None or rank < best[0]:
                    best = rank, index, candidate, setup_gain
        if best is None:
            break
        _, index, candidate, setup_gain = best
        working[index] = candidate
        changed.add(index)
        if setup_gain:
            setup_changed.add(index)
    return SceneRepair(working, frozenset(changed), frozenset(setup_changed))
