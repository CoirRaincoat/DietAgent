"""Public mechanism contracts; assigned vectors are not nutrition or quality scores."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from evaluation.pair_variety_probe import probe_pair_variety

# This historical offline contrast retains its exact pre-migration policy;
# current production defaults are covered by API/health-standard tests.
from tests.historical_health_rules import HistoricalHealthRuleEngine as RuleEngine


def dish(key: str, food: str) -> Recipe:
    return Recipe(
        recipe_id=key,
        name=f"蒸{food}{key}",
        ingredients=[Ingredient(name=food, raw=food)],
        raw_ingredients=food,
        steps=f"{food}蒸熟装盘。",
        categories=["vegetable"],
        meal_types=["晚餐"],
        labels=["清淡"],
        raw_label="晚餐、清淡",
        source_row=1,
        fingerprint=key,
    )


class Vectors(RuleEngine):
    """Only the objective algebra is assigned; safety and source gates remain real."""

    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return {"a": (1, 1), "b": (1, 1), "c": (0, 3), "d": (3, 0)}[recipe.recipe_id]


def pool() -> list[Recipe]:
    return [dish("a", "白菜"), dish("b", "冬瓜"), dish("c", "菠菜"), dish("d", "西兰花")]


def demand() -> Constraints:
    return Constraints(dish_count=2, health_goals=["降压", "护心"])


def test_joint_health_gain_does_not_require_an_existing_named_repeat() -> None:
    records = pool()
    result = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert {r.recipe_id for r in result.recipes} == {"c", "d"}
    exchange = result.exchanges[0]
    assert exchange["configured_goal_sums_before"] == (2, 2)
    assert exchange["configured_goal_sums_after"] == (3, 3)
    assert exchange["named_pairs_before"] == exchange["named_pairs_after"] == 0
    assert exchange["objective"] == "health_reference"
    assert not result.local_edit_changed


@pytest.mark.parametrize("slot", [1, 2])
def test_local_scope_cannot_borrow_compensation_from_another_slot(slot: int) -> None:
    records = pool()
    result = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        replace_slot=slot,
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.recipes == records[:2]
    assert result.local_edit_changed is False


def test_one_slot_ablation_cannot_claim_the_pair_gain() -> None:
    records = pool()
    result = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        max_changed_slots=1,
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.recipes == records[:2]


def test_default_named_objective_remains_exactly_unchanged() -> None:
    records = pool()
    assert probe_pair_variety(records[:2], records, demand(), Vectors()).recipes == records[:2]


@pytest.mark.parametrize("policy", ["additive", "source_reference_types"])
def test_health_objective_cannot_disable_additive_or_source_type_protection(policy: str) -> None:
    records = pool()
    with pytest.raises(ValueError, match="health_reference requires"):
        probe_pair_variety(
            records[:2],
            records,
            demand(),
            Vectors(),
            objective="health_reference",
            goal_policy=policy,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("token", ["辣椒", "花生"])
def test_pair_compensation_cannot_purchase_hard_safety(token: str) -> None:
    records = pool()
    records[2].ingredients.append(Ingredient(name=token, raw=token))
    records[2].raw_ingredients += f"、{token}"
    constraints = demand().model_copy(update={"no_spicy": True, "allergies": ["花生"]})
    result = probe_pair_variety(
        records[:2],
        records,
        constraints,
        Vectors(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.recipes == records[:2]


@pytest.mark.parametrize(
    "mutation", ["salt", "unknown_method", "breakfast", "unknown_food", "role"]
)
def test_health_gain_never_hides_source_caution_or_culinary_evidence_loss(mutation: str) -> None:
    records = pool()
    if mutation == "salt":
        records[2].ingredients.append(Ingredient(name="盐", raw="盐"))
        records[2].raw_ingredients += "、盐"
    elif mutation == "unknown_method":
        records[2].steps = "按设备提示操作。"
    elif mutation == "breakfast":
        records[2].meal_types = ["早餐"]
        records[2].raw_label = "早餐、清淡"
    elif mutation == "role":
        records[2].categories = ["soup"]
    else:
        records[2].ingredients = [Ingredient(name="原料", raw="原料")]
        records[2].raw_ingredients = "原料"
    result = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.recipes == records[:2]


@pytest.mark.parametrize("goals", [[], ["未配置目标"]])
def test_no_configured_health_gain_has_no_authority_to_churn(goals: list[str]) -> None:
    records = pool()
    result = probe_pair_variety(
        records[:2],
        records,
        Constraints(dish_count=2, health_goals=goals),
        RuleEngine(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.recipes == records[:2] and result.exchanges == []


def test_budget_is_global_and_exhaustion_is_not_infeasibility() -> None:
    records = pool()
    result = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        evaluation_limit=1,
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.evaluated == 1
    assert result.status == "evaluation_budget_exhausted_not_infeasible"


def test_successful_result_is_a_fixed_point_and_never_mutates_input() -> None:
    records = pool()
    snapshot = [r.model_dump() for r in records]
    first = probe_pair_variety(
        records[:2],
        records,
        demand(),
        Vectors(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    second = probe_pair_variety(
        first.recipes,
        records,
        demand(),
        Vectors(),
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert second.recipes == first.recipes and second.exchanges == []
    assert [r.model_dump() for r in records] == snapshot


@pytest.mark.parametrize("preference", ["清淡一点别太油", "不要烤", "早点做完"])
def test_unsupported_prose_demand_stays_visible_and_unchanged(preference: str) -> None:
    records = pool()
    constraints = demand().model_copy(update={"preferences": [preference]})
    result = probe_pair_variety(
        records[:2],
        records,
        constraints,
        Vectors(),
        preference_policy="canonical_source_coverage",
        objective="health_reference",
        goal_policy="additive_and_source_reference_types",
    )
    assert result.status == "unsupported_request_kept_unchanged"
    assert result.recipes == records[:2]
