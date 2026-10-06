"""Authored development contracts for an OFF, bounded meal experiment."""

import pytest

from app.domain.models import Constraints, ScopedMethod
from evaluation.pair_variety_probe import probe_pair_variety

# This historical offline contrast retains its exact pre-migration policy;
# current production defaults are covered by API/health-standard tests.
from tests.historical_health_rules import HistoricalHealthRuleEngine as RuleEngine
from tests.test_source_reference_coverage import recipe


def test_equal_additive_scores_cannot_trade_away_the_only_oat_reference():
    menu = [
        recipe("a", "蒸南瓜燕麦", ["南瓜", "燕麦"]),
        recipe("b", "蒸南瓜", ["南瓜"]),
    ]
    candidate = recipe("c", "蒸白菜豆腐", ["白菜", "豆腐"])
    c = Constraints(dish_count=2, health_goals=["护心"])
    # Equal configured sums do not mean that the original food references survived.
    loose = probe_pair_variety(menu, [candidate], c, RuleEngine())
    assert loose.recipes != menu
    strict = probe_pair_variety(
        menu,
        [candidate],
        c,
        RuleEngine(),
        goal_policy="additive_and_source_reference_types",
    )
    assert strict.recipes == [menu[0], candidate]
    assert strict.blockers["positive_source_reference_type_lost"] > 0


def test_source_types_alone_do_not_license_lower_goal_sums():
    menu = [recipe("a", "蒸南瓜", ["南瓜"]), recipe("b", "南瓜饭", ["南瓜", "大米"], "staple")]
    other = recipe("c", "白米饭", ["大米"], "staple")
    c = Constraints(dish_count=2, health_goals=["降压", "护心"])
    strict = probe_pair_variety(
        menu, [other], c, RuleEngine(), goal_policy="additive_and_source_reference_types"
    )
    assert strict.recipes == menu
    assert strict.blockers["whole_menu_goal_lower"] > 0


@pytest.mark.parametrize("slot", [None, 1, 2])
def test_joint_policy_has_a_real_bounded_gain_and_respects_edit_scope(slot):
    menu = [recipe("a", "蒸南瓜", ["南瓜"]), recipe("b", "香蒸南瓜", ["南瓜"])]
    other = recipe("c", "蒸白菜", ["白菜"])
    c = Constraints(dish_count=2, health_goals=["降压", "护心"])
    result = probe_pair_variety(
        menu,
        [other],
        c,
        RuleEngine(),
        replace_slot=slot,
        goal_policy="additive_and_source_reference_types",
    )
    assert other in result.recipes
    if slot:
        assert result.recipes[2 - slot] == menu[2 - slot]
    assert len(result.exchanges) == 1
    e = result.exchanges[0]
    assert e["named_pairs_after"] < e["named_pairs_before"]
    assert e["configured_goal_sums_after"] == e["configured_goal_sums_before"]
    assert e["positive_reference_types_before"] == e["positive_reference_types_after"]
    assert (
        probe_pair_variety(
            result.recipes,
            [other],
            c,
            RuleEngine(),
            goal_policy="additive_and_source_reference_types",
        ).recipes
        == result.recipes
    )


@pytest.mark.parametrize("food", ["辣椒", "花生"])
def test_joint_policy_never_relaxes_hard_safety(food):
    menu = [recipe("a", "蒸南瓜", ["南瓜"]), recipe("b", "香蒸南瓜", ["南瓜"])]
    other = recipe("c", "蒸白菜", ["白菜", food])
    c = Constraints(dish_count=2, no_spicy=True, allergies=["花生"])
    assert (
        probe_pair_variety(
            menu, [other], c, RuleEngine(), goal_policy="additive_and_source_reference_types"
        ).recipes
        == menu
    )


def test_any_probe_policy_preserves_grounded_food_and_slot_method_coverage():
    menu = [recipe("a", "蒸南瓜", ["南瓜"]), recipe("b", "香蒸南瓜", ["南瓜"])]
    other = recipe("c", "蒸白菜", ["白菜"])
    c = Constraints(
        dish_count=2, scoped_methods=[ScopedMethod(food="南瓜", method="蒸", slot=1, required=True)]
    )
    for policy in ("additive", "source_reference_types", "additive_and_source_reference_types"):
        result = probe_pair_variety(menu, [other], c, RuleEngine(), goal_policy=policy)
        assert result.recipes == [menu[0], other]
        assert result.blockers["scoped_method_coverage_lost"] > 0


def test_unsatisfied_required_scope_is_invalid_not_a_quality_win():
    menu = [recipe("a", "蒸南瓜", ["南瓜"]), recipe("b", "香蒸南瓜", ["南瓜"])]
    c = Constraints(
        dish_count=2, scoped_methods=[ScopedMethod(food="白菜", method="蒸", slot=1, required=True)]
    )
    result = probe_pair_variety(
        menu, [recipe("c", "蒸白菜", ["白菜"])], c, RuleEngine(), goal_policy="additive"
    )
    assert result.recipes == menu
    assert result.status == "invalid_or_unresolved_input_kept_unchanged"
