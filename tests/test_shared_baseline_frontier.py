"""Same-budget incumbent controls; authored data, not quality certification."""

import pytest

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine, compact
from evaluation.content_refinement_probe import probe_content_refinement
from evaluation.history_food_exposure import history_food_exposure
from evaluation.shared_baseline_frontier import probe_shared_baseline_frontier
from tests.test_source_health_replay_roles import record


def pool() -> tuple[list[Recipe], list[Recipe]]:
    old = record("蒸白菜", "白菜100克", "白菜蒸熟装盘。")
    carrot = record("蒸胡萝卜", "胡萝卜100克", "胡萝卜蒸熟装盘。")
    copies = [
        old.model_copy(update={"recipe_id": f"copy-{i}", "name": f"另一份白菜{i}"})
        for i in range(6)
    ]
    fresh = record("蒸西兰花", "西兰花100克", "西兰花蒸熟装盘。")
    return [old, carrot], [*copies, fresh]


@pytest.mark.parametrize("limit", [1, 2, 4, 10, 100, 1000])
def test_shared_budget_and_full_standalone_baseline_are_preserved(limit: int) -> None:
    menu, candidates = pool()
    history = [[r.name for r in menu]]
    c = Constraints(dish_count=2)
    rules = RuleEngine()
    baseline = probe_content_refinement(
        menu, candidates, c, rules, history, [menu], pool_limit=3, evaluation_limit=limit
    )
    result = probe_shared_baseline_frontier(
        menu, candidates, c, rules, history, [menu], pool_limit=3, evaluation_limit=limit
    )
    assert result.baseline == baseline
    assert result.evaluated <= limit
    assert result.evaluated == baseline.evaluated + (
        result.extension.evaluated if result.extension else 0
    )
    before = [history_food_exposure(r, [menu]) for r in baseline.recipes]
    after = [history_food_exposure(r, [menu]) for r in result.recipes]
    assert [(o.observed_pairs, o.missing_pairs) for o in after] == [
        (o.observed_pairs, o.missing_pairs) for o in before
    ]
    assert sum(o.weighted_cosine_units or 0 for o in after) <= sum(
        o.weighted_cosine_units or 0 for o in before
    )
    names = {compact(r.name) for r in menu}
    assert sum(compact(r.name) in names for r in result.recipes) <= sum(
        compact(r.name) in names for r in baseline.recipes
    )


def test_extension_can_improve_crowded_flat_pool_without_replacing_incumbent() -> None:
    menu, _ = pool()
    broccoli = record("蒸西兰花", "西兰花100克", "西兰花蒸熟装盘。")
    tomato = record("蒸番茄", "番茄100克", "番茄蒸熟装盘。")
    candidates = [
        broccoli.model_copy(update={"recipe_id": f"broccoli-{i}", "name": f"蒸西兰花{i}"})
        for i in range(6)
    ] + [tomato]
    result = probe_shared_baseline_frontier(
        menu,
        candidates,
        Constraints(dish_count=2),
        RuleEngine(),
        [[r.name for r in menu]],
        [menu],
        pool_limit=3,
        evaluation_limit=1000,
    )
    assert any(r in result.baseline.recipes for r in menu)
    assert not any(r in result.recipes for r in menu), result.extension
    assert result.extension is not None and result.extension.exchanges


@pytest.mark.parametrize("scope", ["local", "continue"])
def test_no_new_scope_and_repeatable_nonmutating_records(scope: str) -> None:
    menu, candidates = pool()
    snapshot = [r.model_dump_json() for r in [*menu, *candidates]]
    result = probe_shared_baseline_frontier(
        menu,
        candidates,
        Constraints(dish_count=2),
        RuleEngine(),
        [[r.name for r in menu]],
        [menu],
        replace_slot=1 if scope == "local" else None,
        recheck_soft_preferences=scope != "continue",
    )
    assert result.recipes == menu and result.evaluated == 0
    assert snapshot == [r.model_dump_json() for r in [*menu, *candidates]]
