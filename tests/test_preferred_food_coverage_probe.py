"""Public development fixtures: whole-meal preference is not a slot mandate."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient
from app.rules.engine import RuleEngine
from evaluation.pair_variety_probe import probe_pair_variety
from tests.test_source_reference_coverage import recipe, trio


def test_opt_in_keeps_pumpkin_once_not_in_every_original_slot() -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, preferred_ingredients=["南瓜"])
    frozen = constraints.model_dump()
    rules = RuleEngine()
    default = probe_pair_variety(
        pool[:2], pool, constraints, rules, goal_policy="source_reference_types"
    )
    experiment = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        rules,
        preferred_food_policy="menu_coverage",
        goal_policy="source_reference_types",
    )
    assert default.recipes == pool[:2]
    assert experiment.recipes == [pool[0], pool[2]]
    assert constraints.model_dump() == frozen
    assert experiment.exchanges[0]["covered_preferred_foods_before"] == ["南瓜"]
    assert experiment.exchanges[0]["covered_preferred_foods_after"] == ["南瓜"]
    assert experiment.exchanges[0]["preferred_food_policy"] == "menu_coverage"


def test_all_previously_covered_food_preferences_remain_not_just_one() -> None:
    pool = trio()
    pool[1].ingredients.append(Ingredient(name="燕麦", raw="燕麦"))
    pool[1].raw_ingredients += "、燕麦"
    constraints = Constraints(dish_count=2, preferred_ingredients=["南瓜", "燕麦"])
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        preferred_food_policy="menu_coverage",
        goal_policy="source_reference_types",
    )
    assert result.recipes == pool[:2]
    assert result.blockers["food_coverage_or_composition_lost"] > 0


@pytest.mark.parametrize("unsafe", ["辣椒", "花生"])
def test_menu_coverage_never_changes_hard_constraints(unsafe: str) -> None:
    pool = trio()
    pool[2].ingredients.append(Ingredient(name=unsafe, raw=unsafe))
    pool[2].raw_ingredients += f"、{unsafe}"
    constraints = Constraints(
        dish_count=2, preferred_ingredients=["南瓜"], no_spicy=True, allergies=["花生"]
    )
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        preferred_food_policy="menu_coverage",
        goal_policy="source_reference_types",
    )
    assert result.recipes == pool[:2]


def test_removing_food_bonus_does_not_relax_meal_fit() -> None:
    pool = trio()
    pool[2].meal_types = ["早餐"]
    constraints = Constraints(dish_count=2, meal_type="晚餐", preferred_ingredients=["南瓜"])
    assert (
        probe_pair_variety(
            pool[:2],
            pool,
            constraints,
            RuleEngine(),
            preferred_food_policy="menu_coverage",
            goal_policy="source_reference_types",
        ).recipes
        == pool[:2]
    )


@pytest.mark.parametrize("slot", [1, 2])
def test_local_observation_reports_actual_change_not_claimed_success(slot: int) -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, preferred_ingredients=["南瓜"])
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        replace_slot=slot,
        preferred_food_policy="menu_coverage",
        goal_policy="source_reference_types",
    )
    assert result.recipes[2 - slot] == pool[2 - slot]
    assert result.local_edit_changed is (slot == 2)
    assert result.disclosures
    assert (
        "未改变指定菜位" in result.disclosures[0]
        if slot == 1
        else "仅改变指定菜位" in result.disclosures[0]
    )


def test_additive_mode_still_keeps_rule_score_even_when_food_bonus_guard_is_omitted() -> None:
    pool = trio()
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2, preferred_ingredients=["南瓜"]),
        RuleEngine(),
        preferred_food_policy="menu_coverage",
    )
    assert result.recipes == pool[:2]
    assert result.blockers["whole_menu_rule_score_lower"] > 0


@pytest.mark.parametrize("unsupported", ["preferences", "max_minutes", "method_meal_priority"])
def test_unhandled_demands_still_refuse_search_and_disclose_local_unchanged(
    unsupported: str,
) -> None:
    constraints = Constraints(
        dish_count=2,
        **{
            unsupported: (
                ["清淡"]
                if unsupported == "preferences"
                else 10 if unsupported == "max_minutes" else "method"
            )
        },
    )
    pool = trio()
    result = probe_pair_variety(
        pool[:2],
        pool,
        constraints,
        RuleEngine(),
        replace_slot=2,
        preferred_food_policy="menu_coverage",
    )
    assert result.status == "unsupported_request_kept_unchanged"
    assert result.local_edit_changed is False
    assert "未改变指定菜位" in result.disclosures[0]


def test_invalid_policy_rejected_instead_of_silently_relaxing_guard() -> None:
    with pytest.raises(ValueError):
        probe_pair_variety(
            trio()[:2],
            trio(),
            Constraints(dish_count=2),
            RuleEngine(),
            preferred_food_policy="unknown",
        )  # type: ignore[arg-type]


def test_production_forced_replacement_is_not_the_post_repair_unchanged_observation() -> None:
    pool = [
        recipe("a", "蒸南瓜", ["南瓜"]),
        recipe("b", "南瓜饭", ["南瓜", "大米"], "staple"),
        recipe("c", "蒸西兰花", ["西兰花"]),
    ]
    constraints = Constraints(dish_count=2, preferred_ingredients=["南瓜"])
    result = MenuPlanner(RuleEngine()).plan(pool, constraints, current=pool[:2], replace_slot=1)
    assert result.failure is None
    assert result.recipes == [pool[2], pool[1]]
    assert any(change["slot"] == 1 for change in result.changes)
