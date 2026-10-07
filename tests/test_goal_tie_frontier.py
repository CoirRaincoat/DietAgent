"""Finite algebra and public probe contrasts, never actual health labels."""

import pytest

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine
from evaluation.goal_tie_frontier import GoalTieFrontier
from evaluation.pair_variety_probe import probe_pair_variety
from tests.test_pair_variety_probe import dish


def test_late_componentwise_better_vector_beats_original_stable_order():
    frontier = GoalTieFrontier[str]()
    frontier.consider((-1, 2, 0), (-1, 5, 4), "old")
    frontier.consider((-1, 2, 99), (1, 5, 4), "better")
    assert frontier.best() == ((-1, 2, 99), "better")
    assert set(frontier.values) == {(1, 5, 4)}


def test_incomparable_goals_keep_stability_not_sum_or_goal_order():
    frontier = GoalTieFrontier[str]()
    frontier.consider((-1, 2, 0), (1, 5), "stable")
    frontier.consider((-1, 2, 9), (4, 4), "larger_sum")
    assert len(frontier.values) == 2
    assert frontier.best() == ((-1, 2, 0), "stable")


@pytest.mark.parametrize("other_rank", [(-1, 2, 0), (0, 1, 0)])
def test_goal_advantage_cannot_override_gain_or_fewer_changes(other_rank):
    frontier = GoalTieFrontier[str]()
    frontier.consider((-1, 1, 9), (0, 0), "primary")
    frontier.consider(other_rank, (99, 99), "goals")
    assert frontier.best() == ((-1, 1, 9), "primary")


def test_better_primary_resets_old_goal_frontier():
    frontier = GoalTieFrontier[str]()
    frontier.consider((-1, 2, 0), (99,), "old")
    frontier.consider((-2, 1, 5), (0,), "new")
    assert frontier.best() == ((-2, 1, 5), "new")


@pytest.mark.parametrize("goals", [(), (0,), (1, 2, 3)])
def test_equal_goal_vectors_use_original_stable_rank(goals):
    frontier = GoalTieFrontier[str]()
    assert frontier.best() is None
    frontier.consider((-1, 1, 9), goals, "late")
    frontier.consider((-1, 1, 0), goals, "first")
    assert len(frontier.values) == 1
    assert frontier.best() == ((-1, 1, 0), "first")


def test_dimensions_and_missing_primary_are_not_silently_compared():
    frontier = GoalTieFrontier[str]()
    with pytest.raises(ValueError):
        frontier.consider((0,), (0,), "bad")
    frontier.consider((-1, 1), (0,), "okay")
    with pytest.raises(ValueError):
        frontier.consider((-1, 1), (1, 2), "bad")


class AssignedTieVectors(RuleEngine):
    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return {"a": (1, 1), "b": (1, 1), "c": (1, 1), "d": (2, 1)}[recipe.recipe_id]


def records():
    return [dish("a", "南瓜"), dish("b", "贝贝南瓜"), dish("c", "西兰花"), dish("d", "冬瓜")]


def test_probe_opt_in_removes_dominated_equal_gain_equal_scope_peer():
    pool = records()
    constraints = Constraints(dish_count=2)
    old = probe_pair_variety(pool[:2], pool, constraints, AssignedTieVectors(), max_changed_slots=1)
    new = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        AssignedTieVectors(),
        max_changed_slots=1,
        tie_policy="source_goal_pareto",
    )
    assert [r.recipe_id for r in old.recipes] == ["c", "b"]
    assert [r.recipe_id for r in new.recipes] == ["d", "b"]
    assert new.exchanges[0]["named_pairs_after"] == old.exchanges[0]["named_pairs_after"] == 0
    assert new.exchanges[0]["slots"] == old.exchanges[0]["slots"] == [1]
    assert new.exchanges[0]["configured_goal_sums_after"] == (3, 2)
    assert "tie_policy" not in old.exchanges[0]


@pytest.mark.parametrize("slot", [1, 2])
def test_local_scope_is_preserved_even_when_other_slot_has_better_vector(slot):
    pool = records()
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2),
        AssignedTieVectors(),
        replace_slot=slot,
        tie_policy="source_goal_pareto",
    )
    assert result.recipes[2 - slot] == pool[2 - slot]
    assert result.exchanges[0]["slots"] == [slot]
    assert result.local_edit_changed


@pytest.mark.parametrize("bad_food", ["辣椒", "花生"])
def test_pareto_cannot_make_unsafe_high_scoring_candidate_eligible(bad_food):
    pool = records()
    pool[3].raw_ingredients = bad_food
    pool[3].ingredients = [
        pool[3].ingredients[0].model_copy(update={"name": bad_food, "raw": bad_food})
    ]
    pool[3].steps = bad_food + "蒸熟装盘。"
    constraints = Constraints(dish_count=2, no_spicy=True, allergies=["花生"])
    result = probe_pair_variety(
        pool[:2], pool, constraints, AssignedTieVectors(), tie_policy="source_goal_pareto"
    )
    assert "d" not in [r.recipe_id for r in result.recipes]


def test_unsupported_requests_and_invalid_menu_remain_unsearched():
    pool = records()
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2, preferences=["清淡但不要盐"]),
        AssignedTieVectors(),
        tie_policy="source_goal_pareto",
        preference_policy="canonical_source_coverage",
    )
    assert result.status == "unsupported_request_kept_unchanged" and result.recipes == pool[:2]
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2, soup_count=1),
        AssignedTieVectors(),
        tie_policy="source_goal_pareto",
    )
    assert result.status == "invalid_or_unresolved_input_kept_unchanged"


def test_truncated_or_budget_limited_frontier_is_not_optimality_claim():
    pool = records()
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2),
        AssignedTieVectors(),
        evaluation_limit=1,
        tie_policy="source_goal_pareto",
    )
    assert result.status == "evaluation_budget_exhausted_not_infeasible"


def test_unknown_tie_policy_is_rejected():
    pool = records()
    with pytest.raises(ValueError):
        probe_pair_variety(
            pool[:2], pool, Constraints(dish_count=2), AssignedTieVectors(), tie_policy="bogus"
        )
