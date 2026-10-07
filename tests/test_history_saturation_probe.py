"""Public authored offline experiments, not independent quality or nutrition gold."""

import pytest

from app.domain.models import Constraints
from evaluation.history_saturation_probe import probe_history_rotation, saturated_goal_scores

# This historical offline contrast retains its exact pre-migration policy;
# current production defaults are covered by API/health-standard tests.
from tests.historical_health_rules import HistoricalHealthRuleEngine as RuleEngine
from tests.test_source_health_replay_roles import record


def pool():
    before = [
        record("清蒸南瓜", "南瓜200克", "南瓜蒸熟装盘。"),
        record("鸡肉蒸胡萝卜", "鸡肉200克；胡萝卜100克", "鸡肉胡萝卜蒸熟装盘。"),
        record("南瓜燕麦饭", "燕麦200克；南瓜100克", "燕麦南瓜煮熟装盘。"),
    ]
    fresh = [
        record("清蒸白菜", "白菜200克", "白菜蒸熟装盘。"),
        record("清蒸鸡胸肉", "鸡胸肉200克", "鸡胸肉蒸熟装盘。"),
        record("燕麦饭", "燕麦200克", "燕麦煮熟装盘。"),
    ]
    return before, fresh


def constraints():
    return Constraints(health_goals=["降压", "护心"], no_spicy=True, meal_type="晚餐", dish_count=3)


def test_duplicate_positive_credit_is_capped_but_source_types_are_retained():
    before, fresh = pool()
    c, rules = constraints(), RuleEngine()
    history = [[r.name for r in before]]
    strict = probe_history_rotation(before, [*before, *fresh], c, rules, history)
    relaxed = probe_history_rotation(
        before, [*before, *fresh], c, rules, history, policy="saturated_types"
    )
    assert strict.recipes[1:] == before[1:]
    assert relaxed.recipes == fresh
    assert (
        saturated_goal_scores(before, c, rules) == saturated_goal_scores(fresh, c, rules) == (2, 4)
    )
    assert relaxed.exchanges
    assert any(e["raw_goal_sums_before"] != e["raw_goal_sums_after"] for e in relaxed.exchanges)
    assert relaxed.exchanges[-1]["history_cost_after"] == 0
    assert (
        probe_history_rotation(
            before, [*before, *fresh], c, rules, history, policy="saturated_types"
        ).recipes
        == relaxed.recipes
    )


def test_penalties_are_not_saturated_when_caution_tokens_spread_to_more_dishes():
    c, rules = constraints(), RuleEngine()
    concentrated = [
        record("清蒸白菜", "白菜200克；盐1克；生抽1克", "蒸熟装盘。"),
        record("蒸鸡肉", "鸡肉200克", "蒸熟装盘。"),
    ]
    spread = [
        record("蒸白菜", "白菜200克；盐1克", "蒸熟装盘。"),
        record("鸡肉", "鸡肉200克；生抽1克", "蒸熟装盘。"),
    ]
    assert saturated_goal_scores(concentrated, c, rules) == (-1, -1)
    assert saturated_goal_scores(spread, c, rules) == (-4, -4)
    probe = probe_history_rotation(
        concentrated,
        spread,
        c.model_copy(update={"dish_count": 2}),
        rules,
        [[r.name for r in concentrated]],
        policy="saturated_types",
    )
    assert probe.recipes != spread
    assert all(
        a >= b
        for a, b in zip(
            saturated_goal_scores(probe.recipes, c, rules),
            saturated_goal_scores(concentrated, c, rules),
            strict=True,
        )
    )
    assert probe.blockers["saturated_goal_lower"] > 0


def test_losing_sole_oat_type_cannot_be_paid_for_by_other_preferred_food():
    before, fresh = pool()
    c, rules = constraints(), RuleEngine()
    alternate = record("全麦面包", "全麦面粉200克", "面粉揉成面团，烤熟装盘。")
    probe = probe_history_rotation(
        before,
        [fresh[0], fresh[1], alternate],
        c,
        rules,
        [[r.name for r in before]],
        policy="saturated_types",
    )
    assert probe.recipes[2] == before[2]
    assert probe.blockers["unique_positive_type_lost"] > 0


@pytest.mark.parametrize(
    "danger",
    [
        ("花生鸡肉", "鸡胸肉100克；花生20克", "蒸熟装盘。"),
        ("辣椒鸡肉", "鸡胸肉100克；辣椒20克", "蒸熟装盘。"),
        ("甜饼干", "面粉100克；糖粉10克；黄油20克", "烤熟装盘。"),
    ],
)
def test_saturation_never_waives_allergy_nonspicy_or_meal_gate(danger):
    before, fresh = pool()
    c = constraints().model_copy(update={"allergies": ["花生"]})
    sample = record(*danger)
    probe = probe_history_rotation(
        before,
        [*fresh, sample],
        c,
        RuleEngine(),
        [[r.name for r in before]],
        policy="saturated_types",
    )
    assert sample not in probe.recipes


def test_new_caution_label_cannot_be_paid_for_with_unseen_recipe_names():
    before, _ = pool()
    salted = record("盐蒸鸡肉", "鸡肉100克；盐1克", "蒸熟装盘。")
    probe = probe_history_rotation(
        before,
        [salted],
        constraints(),
        RuleEngine(),
        [[r.name for r in before]],
        policy="saturated_types",
    )
    assert probe.recipes == before


@pytest.mark.parametrize("mode", ["local", "continue", "no_history", "unknown_preference", "time"])
def test_unsupported_or_non_newmeal_scope_keeps_menu(mode):
    before, fresh = pool()
    c = constraints()
    kwargs = {}
    history = [[r.name for r in before]]
    if mode == "local":
        kwargs["replace_slot"] = 2
    elif mode == "continue":
        kwargs["recheck_soft_preferences"] = False
    elif mode == "no_history":
        history = []
    elif mode == "unknown_preference":
        c = c.model_copy(update={"preferences": ["不要酸"]})
    elif mode == "time":
        c = c.model_copy(update={"max_minutes": 10})
    probe = probe_history_rotation(
        before, fresh, c, RuleEngine(), history, policy="saturated_types", **kwargs
    )
    assert probe.recipes == before and not probe.exchanges


def test_budget_exhaustion_is_not_reported_as_catalog_fixed_point():
    before, fresh = pool()
    probe = probe_history_rotation(
        before,
        fresh,
        constraints(),
        RuleEngine(),
        [[r.name for r in before]],
        policy="saturated_types",
        evaluation_limit=1,
    )
    assert probe.evaluated == 1
    assert probe.status == "budget_exhausted_best_observed_NOT_optimal"


@pytest.mark.parametrize(
    "update", [{"dish_count": 4}, {"soup_count": 1}, {"allergies": ["未知特殊粉"]}]
)
def test_invalid_or_unresolved_meal_is_not_repaired_by_this_experiment(update):
    before, fresh = pool()
    probe = probe_history_rotation(
        before,
        fresh,
        constraints().model_copy(update=update),
        RuleEngine(),
        [[r.name for r in before]],
        policy="saturated_types",
    )
    assert probe.recipes == before and probe.status == "invalid_input_kept_unchanged"


def test_query_source_identity_is_not_traded_for_equal_total_lexical_points():
    before, fresh = pool()
    probe = probe_history_rotation(
        before,
        fresh,
        constraints(),
        RuleEngine(),
        [[r.name for r in before]],
        policy="saturated_types",
        query_terms=["南瓜"],
    )
    assert any("南瓜" in r.name for r in probe.recipes)


def test_unknown_goal_does_not_create_saturated_reference_points():
    before, _ = pool()
    assert saturated_goal_scores(before, Constraints(health_goals=["自创功效"]), RuleEngine()) == (
        0,
    )
