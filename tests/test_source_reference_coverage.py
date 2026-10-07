"""Development source-reference fixtures, not independent clinical labels."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from evaluation.pair_variety_probe import probe_pair_variety
from evaluation.source_reference_coverage import positive_reference_labels

# This historical offline contrast retains its exact pre-migration policy;
# current production defaults are covered by API/health-standard tests.
from tests.historical_health_rules import HistoricalHealthRuleEngine as RuleEngine


def recipe(key: str, name: str, foods: list[str], role: str = "vegetable") -> Recipe:
    return Recipe(
        recipe_id=key,
        name=name,
        raw_ingredients="、".join(foods),
        ingredients=[Ingredient(name=f, raw=f) for f in foods],
        categories=[role],
        steps="食材蒸熟装盘。",
        meal_types=["晚餐"],
        source_row=1,
        fingerprint=key,
    )


def trio() -> list[Recipe]:
    return [
        recipe("a", "蒸南瓜", ["南瓜"]),
        recipe("b", "南瓜饭", ["南瓜", "大米"], "staple"),
        recipe("c", "白米饭", ["大米"], "staple"),
    ]


def test_reference_mode_discloses_additive_fall_but_retains_original_goal_types() -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, health_goals=["降压", "护心"])
    rules = RuleEngine()
    additive = probe_pair_variety(pool[:2], pool, constraints, rules, max_changed_slots=1)
    coverage = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        rules,
        max_changed_slots=1,
        goal_policy="source_reference_types",
    )
    assert additive.recipes == pool[:2]
    assert coverage.recipes == [pool[0], pool[2]]
    exchange = coverage.exchanges[0]
    assert exchange["configured_goal_sums_before"] == (4, 4)
    assert exchange["configured_goal_sums_after"] == (2, 2)
    assert exchange["positive_reference_types_before"] == exchange["positive_reference_types_after"]
    assert exchange["goal_policy"] == "source_reference_types"


def test_a_different_preferred_food_may_not_replace_the_only_oat_reference() -> None:
    pool = trio()
    pool[1].ingredients.append(Ingredient(name="燕麦", raw="燕麦"))
    pool[1].raw_ingredients += "、燕麦"
    pool[2].ingredients.append(Ingredient(name="豆腐", raw="豆腐"))
    pool[2].raw_ingredients += "、豆腐"
    constraints = Constraints(dish_count=2, health_goals=["护心"])
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        max_changed_slots=1,
        goal_policy="source_reference_types",
    )
    assert result.recipes == pool[:2]
    assert result.blockers["positive_source_reference_type_lost"] > 0


def test_reference_mode_does_not_pay_for_new_cautions_with_retained_positive_types() -> None:
    pool = trio()
    pool[2].ingredients.append(Ingredient(name="盐", raw="盐"))
    pool[2].raw_ingredients += "、盐"
    constraints = Constraints(dish_count=2, health_goals=["降压"])
    result = probe_pair_variety(
        pool[:2], pool, constraints, RuleEngine(), goal_policy="source_reference_types"
    )
    assert result.recipes == pool[:2]
    assert result.blockers["declared_caution_frequency_increased"] > 0


@pytest.mark.parametrize("local", [1, 2])
def test_local_request_never_changes_unaddressed_slot(local: int) -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, health_goals=["降压"])
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        replace_slot=local,
        goal_policy="source_reference_types",
    )
    assert result.recipes[2 - local] == pool[2 - local]
    assert result.recipes == ([pool[0], pool[2]] if local == 2 else pool[:2])


@pytest.mark.parametrize("unsafe", ["辣椒", "花生"])
def test_reference_mode_keeps_hard_safety_gates(unsafe: str) -> None:
    pool = trio()
    pool[2].raw_ingredients += f"、{unsafe}"
    pool[2].ingredients.append(Ingredient(name=unsafe, raw=unsafe))
    constraints = Constraints(
        dish_count=2, health_goals=["降压"], no_spicy=True, allergies=["花生"]
    )
    assert (
        probe_pair_variety(
            pool[:2], pool, constraints, RuleEngine(), goal_policy="source_reference_types"
        ).recipes
        == pool[:2]
    )


def test_labels_preserve_goal_and_each_configured_preferred_term_identity() -> None:
    record = recipe("a", "蒸豆腐燕麦", ["豆腐", "燕麦"])
    constraints = Constraints(health_goals=["护心", "降压", "未知目标", "护心"])
    assert positive_reference_labels(record, constraints, RuleEngine()) == frozenset(
        {"护心:preferred_term:豆腐", "护心:preferred_term:燕麦"}
    )


def test_role_disabled_bonus_does_not_become_reference_coverage() -> None:
    record = recipe("a", "鸡肉饭", ["鸡肉", "大米"], "staple")
    assert (
        positive_reference_labels(record, Constraints(health_goals=["增肌"]), RuleEngine())
        == frozenset()
    )


def test_titles_and_health_labels_cannot_create_reference_evidence() -> None:
    record = recipe("a", "燕麦护心降压健康饭", ["未知配料"], "staple")
    record.labels = ["护心", "降压"]
    assert (
        positive_reference_labels(record, Constraints(health_goals=["护心", "降压"]), RuleEngine())
        == frozenset()
    )


def test_source_finishing_method_is_a_separate_configured_reference() -> None:
    record = recipe("a", "蒸南瓜", ["南瓜"])
    labels = positive_reference_labels(record, Constraints(health_goals=["减脂"]), RuleEngine())
    assert labels == frozenset({"减脂:category_reference", "减脂:method:蒸"})
    record.steps = "放入蒸锅备用。"
    assert "减脂:method:蒸" not in positive_reference_labels(
        record, Constraints(health_goals=["减脂"]), RuleEngine()
    )


def test_explicit_food_coverage_survives_reference_ablation() -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, health_goals=["降压"], preferred_ingredients=["南瓜"])
    result = probe_pair_variety(
        pool[:2], pool, constraints, RuleEngine(), goal_policy="source_reference_types"
    )
    # Per-slot lexical protection is stricter than whole-menu food coverage:
    # it keeps the preferred pumpkin in both existing slots in this fixture.
    assert result.recipes == pool[:2]
    assert any(RuleEngine().food_matches(r, "南瓜") for r in result.recipes)


def test_joint_relocation_keeps_oat_reference_and_salt_frequency_with_method_spread() -> None:
    pool = [
        recipe("a", "燕麦南瓜泥", ["南瓜", "燕麦", "盐"]),
        recipe("b", "香蒸贝贝南瓜", ["贝贝南瓜"]),
        recipe("c", "南瓜饭", ["南瓜", "大米"], "staple"),
        recipe("d", "娃娃菜炒香菇", ["娃娃菜", "香菇", "盐"]),
        recipe("e", "蒸燕麦饭", ["燕麦", "大米"], "staple"),
    ]
    pool[2].steps = "南瓜和大米煮熟装盘。"
    pool[3].steps = "娃娃菜和香菇炒熟装盘。"
    constraints = Constraints(dish_count=3, health_goals=["降压", "护心"])
    rules = RuleEngine()
    single = probe_pair_variety(
        pool[:3],
        pool,
        constraints,
        rules,
        max_changed_slots=1,
        goal_policy="source_reference_types",
    )
    pair = probe_pair_variety(
        pool[:3], pool, constraints, rules, goal_policy="source_reference_types"
    )
    additive = probe_pair_variety(pool[:3], pool, constraints, rules)
    assert single.recipes == additive.recipes == pool[:3]
    assert pair.recipes == [pool[3], pool[1], pool[4]]
    exchange = pair.exchanges[0]
    assert exchange["slots"] == [1, 3]
    assert exchange["named_pairs_before"] == 3 and exchange["named_pairs_after"] == 0
    assert exchange["caution_counts_before"] == exchange["caution_counts_after"]
    assert exchange["positive_reference_types_before"] == exchange["positive_reference_types_after"]
    assert (
        probe_pair_variety(
            pair.recipes, pool, constraints, rules, goal_policy="source_reference_types"
        ).recipes
        == pair.recipes
    )
