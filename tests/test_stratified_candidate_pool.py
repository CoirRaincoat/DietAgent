"""Authored bounded-pool controls; not independent menu quality labels."""

import pytest

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine
from evaluation.content_refinement_probe import probe_content_refinement
from evaluation.history_saturation_probe import probe_history_rotation
from evaluation.stratified_candidate_pool import bound_declared_food_pool
from tests.test_source_health_replay_roles import record


def crowded() -> tuple[Recipe, list[Recipe], Recipe]:
    old = record("蒸白菜", "白菜100克", "白菜蒸熟装盘。")
    renamed = record("另一份蒸白菜", "白菜100克", "白菜蒸熟装盘。")
    fresh = record("蒸西兰花", "西兰花100克", "西兰花蒸熟装盘。")
    copies = [
        renamed.model_copy(update={"recipe_id": f"copy-{i}", "name": f"蒸白菜做法{i}"})
        for i in range(6)
    ]
    return old, copies, fresh


def test_bound_pool_reserves_old_and_one_representative_per_declared_food() -> None:
    old, copies, fresh = crowded()
    pool = [*copies, fresh, old]
    signatures = {r.recipe_id: frozenset({"cabbage"}) for r in [old, *copies]}
    signatures[fresh.recipe_id] = frozenset({"broccoli"})
    assert bound_declared_food_pool(pool, old, signatures, 3) == [old, copies[0], fresh]


@pytest.mark.parametrize("limit", [1, 2, 3, 6, 10])
def test_bound_is_deterministic_unique_and_does_not_mutate(limit: int) -> None:
    old, copies, fresh = crowded()
    pool = [*copies, fresh, old]
    snapshot = list(pool)
    signatures: dict[str, frozenset[str]] = {r.recipe_id: frozenset() for r in pool}
    result = bound_declared_food_pool(pool, old, signatures, limit)
    assert result == bound_declared_food_pool(pool, old, signatures, limit)
    assert len(result) == min(limit, len(pool))
    assert result[0] == old
    assert len({r.recipe_id for r in result}) == len(result)
    assert pool == snapshot
    # Unknown declarations share one bucket, not a novel bucket per name.
    assert result == [old, *pool[:-1]][:limit]


def test_name_objective_not_misreported_as_content_gain_and_refinement_preserved() -> None:
    old, copies, fresh = crowded()
    flat = probe_history_rotation(
        [old],
        [*copies, fresh],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        pool_limit=3,
        evaluation_limit=100,
    )
    stratified = probe_history_rotation(
        [old],
        [*copies, fresh],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        pool_strategy="declared_food_stratified",
        pool_limit=3,
        evaluation_limit=100,
    )
    # Name objective alone still selects the earliest zero-exposure name.
    assert flat.recipes == stratified.recipes == [copies[0]]
    refined = probe_content_refinement(
        [old],
        [*copies, fresh],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        [[old]],
        pool_limit=3,
        evaluation_limit=100,
        pool_strategy="declared_food_stratified",
    )
    assert refined.recipes == [fresh]
    assert refined.evaluated <= 100


@pytest.mark.parametrize("invalid", ["limit", "old_missing", "duplicate"])
def test_helper_rejects_invalid_prevalidated_pool(invalid: str) -> None:
    old, copies, fresh = crowded()
    pool = [old, copies[0], fresh]
    if invalid == "old_missing":
        pool = copies
    elif invalid == "duplicate":
        pool.append(copies[0])
    signatures = {r.recipe_id: frozenset({"cabbage"}) for r in pool}
    with pytest.raises(ValueError):
        bound_declared_food_pool(pool, old, signatures, 0 if invalid == "limit" else 3)


def test_stratification_cannot_admit_allergic_candidate_or_change_role() -> None:
    old, copies, fresh = crowded()
    allergic = record("蒸虾仁白菜", "白菜100克；虾仁30克", "上述食材蒸熟。")
    protein = record("清蒸鸡胸", "鸡胸肉100克", "鸡胸肉蒸熟。")
    result = probe_history_rotation(
        [old],
        [allergic, protein, *copies, fresh],
        Constraints(dish_count=1, allergies=["虾"], no_spicy=True),
        RuleEngine(),
        [[old.name]],
        pool_strategy="declared_food_stratified",
    )
    assert all(r.categories == old.categories for r in result.recipes)
    assert not any(r.recipe_id in {allergic.recipe_id, protein.recipe_id} for r in result.recipes)


def test_two_slot_joint_search_can_escape_same_food_pool_crowding() -> None:
    old, copies, fresh = crowded()
    carrot = record("蒸胡萝卜", "胡萝卜100克", "胡萝卜蒸熟装盘。")
    menu = [old, carrot]
    c = Constraints(dish_count=2)
    rules = RuleEngine()
    history = [[r.name for r in menu]]
    flat = probe_history_rotation(
        menu, [*copies, fresh], c, rules, history, pool_limit=3, evaluation_limit=100
    )
    stratified = probe_history_rotation(
        menu,
        [*copies, fresh],
        c,
        rules,
        history,
        pool_limit=3,
        evaluation_limit=100,
        pool_strategy="declared_food_stratified",
    )
    assert carrot in flat.recipes
    assert stratified.recipes == [copies[0], fresh]
    assert stratified.evaluated <= 100


@pytest.mark.parametrize("scope", ["local", "continue"])
def test_stratified_pool_does_not_expand_authority(scope: str) -> None:
    old, copies, fresh = crowded()
    result = probe_history_rotation(
        [old],
        [*copies, fresh],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        pool_strategy="declared_food_stratified",
        replace_slot=1 if scope == "local" else None,
        recheck_soft_preferences=scope != "continue",
    )
    assert result.recipes == [old] and result.evaluated == 0


def test_unknown_pool_strategy_rejected() -> None:
    old, copies, _ = crowded()
    with pytest.raises(ValueError, match="unsupported"):
        probe_history_rotation(
            [old],
            copies,
            Constraints(dish_count=1),
            RuleEngine(),
            [[old.name]],
            pool_strategy="random",  # type: ignore[arg-type]
        )
