"""Public mechanism fixtures; assigned vectors are not health labels or scores."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from evaluation.pair_variety_probe import probe_pair_variety

# This historical offline contrast retains its exact pre-migration policy;
# current production defaults are covered by API/health-standard tests.
from tests.historical_health_rules import HistoricalHealthRuleEngine as RuleEngine


def dish(key: str, food: str) -> Recipe:
    return Recipe(
        recipe_id=key,
        name=f"清蒸{food}{key}",
        raw_ingredients=food,
        ingredients=[Ingredient(name=food, raw=food)],
        steps=f"{food}蒸熟装盘。",
        categories=["vegetable"],
        meal_types=["晚餐"],
        source_row=1,
        fingerprint=key,
    )


class AssignedVectors(RuleEngine):
    """Finite algebra-only fixture; actual hard screening and cautions still run."""

    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return {"a": (1, 1), "b": (1, 1), "c": (0, 2), "d": (2, 0)}[recipe.recipe_id]


def records() -> list[Recipe]:
    return [dish("a", "南瓜"), dish("b", "贝贝南瓜"), dish("c", "西兰花"), dish("d", "冬瓜")]


def test_pair_compensation_retains_each_whole_menu_goal_not_each_slot() -> None:
    pool = records()
    result = probe_pair_variety(pool[:2], pool, Constraints(dish_count=2), AssignedVectors())
    assert {r.recipe_id for r in result.recipes} == {"c", "d"}
    assert result.exchanges[0]["slots"] == [1, 2]
    assert result.exchanges[0]["configured_goal_sums_before"] == (2, 2)
    assert result.exchanges[0]["configured_goal_sums_after"] == (2, 2)
    assert result.exchanges[0]["named_pairs_after"] == 0
    assert (
        probe_pair_variety(
            result.recipes, pool, Constraints(dish_count=2), AssignedVectors()
        ).recipes
        == result.recipes
    )


def test_local_scope_cannot_borrow_goal_compensation_from_second_slot() -> None:
    pool = records()
    result = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2), AssignedVectors(), replace_slot=1
    )
    assert result.recipes == pool[:2]
    assert result.blockers["whole_menu_goal_lower"] > 0


def test_single_exchange_ablation_does_not_claim_pair_mechanism_gain() -> None:
    pool = records()
    result = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2), AssignedVectors(), max_changed_slots=1
    )
    assert result.recipes == pool[:2]
    assert result.exchanges == []


def test_single_peer_can_change_only_addressed_slot_when_goals_equal() -> None:
    pool = records()
    result = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2), RuleEngine(), replace_slot=2
    )
    assert result.recipes[0] == pool[0]
    assert result.recipes[1] == pool[2]


@pytest.mark.parametrize("unsafe", ["辣椒", "花生"])
def test_hard_constraints_independently_filter_unsafe_peers(unsafe: str) -> None:
    pool = records()[:3]
    pool[2].raw_ingredients += f"、{unsafe}"
    pool[2].ingredients.append(Ingredient(name=unsafe, raw=unsafe))
    constraints = Constraints(dish_count=2, no_spicy=True, allergies=["花生"])
    assert probe_pair_variety(pool[:2], pool, constraints, RuleEngine()).recipes == pool[:2]


def test_caution_frequency_is_not_bought_with_compensating_positive_vectors() -> None:
    pool = records()
    pool[2].ingredients.append(Ingredient(name="盐", raw="盐"))
    pool[2].raw_ingredients += "、盐"
    result = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2, health_goals=["降压"]), AssignedVectors()
    )
    assert result.recipes == pool[:2]
    assert result.blockers["declared_caution_frequency_increased"] > 0


@pytest.mark.parametrize("blocked", ["food", "role", "meal", "unknown_method", "unknown_family"])
def test_peer_protections_do_not_disappear_in_pair_search(blocked: str) -> None:
    pool = records()[:3]
    constraints = Constraints(dish_count=2)
    if blocked == "food":
        constraints.preferred_ingredients = ["贝贝南瓜"]
        pool[0].ingredients = [Ingredient(name="南瓜", raw="南瓜")]
        # Neither a slot swap nor a pair may remove the only exact preferred source.
        result = probe_pair_variety(pool[:2], pool, constraints, RuleEngine(), replace_slot=2)
        assert result.recipes == pool[:2]
        return
    if blocked == "role":
        pool[2].categories = ["protein"]
    elif blocked == "meal":
        pool[2].meal_types = ["早餐"]
    elif blocked == "unknown_method":
        pool[2].steps = "处理后即可。"
    else:
        pool[2].name = "清蒸未知菜"
        pool[2].raw_ingredients = "未知菜"
        pool[2].ingredients = [Ingredient(name="未知菜", raw="未知菜")]
    assert probe_pair_variety(pool[:2], pool, constraints, RuleEngine()).recipes == pool[:2]


@pytest.mark.parametrize(
    "change", [{"preferences": ["不要酸"]}, {"max_minutes": 20}, {"method_meal_priority": "method"}]
)
def test_unsupported_demands_are_explicit_and_leave_input_unchanged(
    change: dict[str, object],
) -> None:
    pool = records()
    constraints = Constraints.model_validate({"dish_count": 2, **change})
    result = probe_pair_variety(pool[:2], pool, constraints, RuleEngine())
    assert result.status == "unsupported_request_kept_unchanged"
    assert result.evaluated == 0
    assert result.recipes == pool[:2]


def test_budget_and_pool_limits_are_disclosed_not_claimed_infeasible() -> None:
    pool = records()
    result = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2), AssignedVectors(), evaluation_limit=1
    )
    assert result.status == "evaluation_budget_exhausted_not_infeasible"
    assert result.evaluated == 1
    capped = probe_pair_variety(
        pool[:2], pool, Constraints(dish_count=2), AssignedVectors(), pool_limit=1
    )
    assert capped.truncated_pools > 0
    assert capped.status == "truncated_pool_fixed_point_not_infeasible"


def test_invalid_menu_and_unknown_allergy_do_not_enter_search() -> None:
    pool = records()
    for constraints in (
        Constraints(dish_count=3),
        Constraints(dish_count=2, soup_count=1),
        Constraints(dish_count=2, allergies=["神秘酱料"]),
    ):
        result = probe_pair_variety(pool[:2], pool, constraints, RuleEngine())
        assert result.status == "invalid_or_unresolved_input_kept_unchanged"
        assert result.recipes == pool[:2]
    with pytest.raises(ValueError):
        probe_pair_variety(pool[:2], pool, Constraints(dish_count=2), RuleEngine(), replace_slot=3)


def test_sources_and_constraints_are_not_mutated() -> None:
    pool = records()
    constraints = Constraints(dish_count=2)
    frozen = [r.model_dump() for r in pool], constraints.model_dump()
    probe_pair_variety(pool[:2], pool, constraints, AssignedVectors())
    assert frozen == ([r.model_dump() for r in pool], constraints.model_dump())
