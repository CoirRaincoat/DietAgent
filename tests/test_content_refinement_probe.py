"""Finite baseline-retention controls, not independent quality evaluation."""

import pytest

from app.domain.meal_history import recommendation_counts
from app.domain.models import Constraints
from app.rules.engine import RuleEngine, compact
from evaluation.content_refinement_probe import probe_content_refinement
from tests.test_content_history_ranking import vegetables


def run(menu, candidates, history, **kwargs):
    return probe_content_refinement(
        menu,
        candidates,
        Constraints(dish_count=len(menu)),
        RuleEngine(),
        [[r.name for r in m] for m in history],
        history,
        **kwargs,
    )


def test_refinement_keeps_name_baseline_and_improves_same_food_renaming():
    old, renamed, fresh = vegetables()
    result = run([old], [renamed, fresh], [[old]])
    assert result.baseline.recipes == [renamed]
    assert result.recipes == [fresh]
    assert result.refinement is not None
    assert result.evaluated == result.baseline.evaluated + result.refinement.evaluated
    assert (
        result.refinement.exchanges[-1]["history_cost_before"]
        == result.refinement.exchanges[-1]["history_cost_after"]
        == 0
    )


def test_refinement_cannot_increase_names_relative_to_computed_baseline():
    old, renamed, fresh = vegetables()
    history = [[old], [old], [old], [fresh]]
    result = run([old], [renamed, fresh], history)
    costs = recommendation_counts([[r.name for r in m] for m in history])
    def name_cost(records):
        return sum(costs[compact(r.name)] for r in records)
    assert name_cost(result.recipes) <= name_cost(result.baseline.recipes) == 0
    assert result.recipes == [renamed]


@pytest.mark.parametrize("limit", [1, 2, 4, 5, 8])
def test_budget_is_shared_and_never_renewed(limit):
    old, renamed, fresh = vegetables()
    result = run([old], [renamed, fresh], [[old]], evaluation_limit=limit)
    assert result.evaluated <= limit
    if result.refinement is None:
        assert result.recipes == result.baseline.recipes
        assert result.status == "baseline_used_shared_budget_NOT_optimal"
    else:
        assert result.evaluated == result.baseline.evaluated + result.refinement.evaluated


@pytest.mark.parametrize("mode", ["local", "continue"])
def test_two_stages_cannot_expand_local_or_continuation_authority(mode):
    old, renamed, fresh = vegetables()
    kwargs = {"replace_slot": 1} if mode == "local" else {"recheck_soft_preferences": False}
    result = run([old], [renamed, fresh], [[old]], **kwargs)
    assert result.recipes == result.baseline.recipes == [old]
    assert result.evaluated == 0


def test_mismatched_history_records_refused_before_search():
    old, _, fresh = vegetables()
    with pytest.raises(ValueError, match="historical recipe"):
        probe_content_refinement(
            [old], [fresh], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[fresh]]
        )
