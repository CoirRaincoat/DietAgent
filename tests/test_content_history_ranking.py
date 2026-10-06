"""Authored content-rotation contracts, not independent quality labels."""

import pytest

from app.domain.models import Constraints, Ingredient
from app.rules.engine import RuleEngine
from evaluation.history_food_exposure import COSINE_UNIT, history_food_exposure
from evaluation.history_saturation_probe import probe_history_rotation
from tests.test_history_scene_retention import tagged
from tests.test_source_health_replay_roles import record


def vegetables():
    old = record("蒸白菜", "白菜100克", "白菜蒸熟装盘。")
    renamed = record("另一份蒸白菜", "白菜100克", "白菜蒸熟装盘。")
    fresh = record("蒸西兰花", "西兰花100克", "西兰花蒸熟装盘。")
    return old, renamed, fresh


def probe(menu, candidates, history, **kwargs):
    c = kwargs.pop("constraints", Constraints(dish_count=len(menu), meal_type="午餐"))
    return probe_history_rotation(
        menu,
        candidates,
        c,
        RuleEngine(),
        [[r.name for r in m] for m in history],
        ranking="declared_content",
        history_menus=history,
        **kwargs,
    )


def test_same_input_name_rotation_renames_food_content_rotation_changes_it():
    old, renamed, fresh = vegetables()
    baseline = probe_history_rotation(
        [old], [renamed, fresh], Constraints(dish_count=1), RuleEngine(), [[old.name]]
    )
    assert baseline.recipes == [renamed]
    result = probe([old], [renamed, fresh], [[old]])
    assert result.recipes == [fresh]
    assert result.exchanges[-1]["weighted_content_units_after"] == 0
    assert result.exchanges[-1]["observed_history_pairs"] == [1]
    assert result.exchanges[-1]["missing_history_pairs"] == [0]


def test_content_rotation_can_help_when_name_exposure_is_already_zero():
    old, renamed, fresh = vegetables()
    result = probe([renamed], [old, fresh], [[old]])
    assert result.recipes == [fresh]
    assert (
        result.exchanges[-1]["history_cost_before"]
        == result.exchanges[-1]["history_cost_after"]
        == 0
    )


def test_content_gain_does_not_authorize_increased_name_exposure():
    old, renamed, fresh = vegetables()
    # A recently exposed broccoli dish is not an admissible escape from a
    # never-exposed cabbage name, even if its food similarity sum is lower.
    result = probe([renamed], [fresh], [[old], [old], [old], [fresh]])
    assert result.recipes == [renamed]


def test_unknown_candidate_not_rewarded_as_zero_content():
    old, _, _ = vegetables()
    unknown = old.model_copy(deep=True, update={"recipe_id": "unknown", "name": "另一道蒸菜"})
    unknown.ingredients = [Ingredient(name="蔬菜碎", raw="蔬菜碎100克")]
    result = probe([old], [unknown], [[old]])
    assert result.recipes == [old]
    assert result.blockers["content_coverage_pool_rejected"] > 0


def test_all_unknown_history_is_explicitly_unobserved_not_diverse():
    old, _, fresh = vegetables()
    unknown = old.model_copy(deep=True)
    unknown.ingredients = [Ingredient(name="蔬菜碎", raw="蔬菜碎100克")]
    result = probe([old], [fresh], [[unknown]])
    assert result.recipes == [old] and not result.exchanges
    assert result.status == "no_observed_content_kept_unchanged"


def test_unknown_only_slot_stays_fixed_without_blocking_observed_slot_rotation():
    old, _, fresh = vegetables()
    unknown = record("蒸鸭肉", "鸭肉100克", "鸭肉蒸熟装盘。")
    chicken = record("蒸鸡肉", "鸡胸肉100克", "鸡肉蒸熟装盘。")
    result = probe([old, unknown], [fresh, chicken], [[old, unknown]])
    assert result.recipes == [fresh, unknown]
    assert result.exchanges[-1]["observed_history_pairs"] == [1, 0]
    assert result.exchanges[-1]["missing_history_pairs"] == [0, 1]


@pytest.mark.parametrize("goals", [["降压", "护心"], ["降压"]])
def test_content_objective_does_not_waive_any_additive_health_dimension(goals):
    from tests.test_history_saturation_probe import pool

    old, fresh = pool()
    constraints = Constraints(dish_count=3, health_goals=goals, no_spicy=True)
    rules = RuleEngine()
    result = probe(old, fresh, [old], constraints=constraints)
    before = [rules.soft_goal_scores(r, constraints) for r in old]
    after = [rules.soft_goal_scores(r, constraints) for r in result.recipes]
    assert all(sum(v[i] for v in after) >= sum(v[i] for v in before) for i in range(len(before[0])))


def test_missing_historical_pairs_remain_missing_and_counts_fixed():
    old, _, fresh = vegetables()
    unknown = old.model_copy(deep=True, update={"name": "历史未知菜"})
    unknown.ingredients = [Ingredient(name="蔬菜碎", raw="蔬菜碎100克")]
    result = probe([old], [fresh], [[old, unknown]])
    assert result.recipes == [fresh]
    assert result.exchanges[-1]["observed_history_pairs"] == [1]
    assert result.exchanges[-1]["missing_history_pairs"] == [1]


@pytest.mark.parametrize("historical", [None, [], [[]]])
def test_content_ranking_rejects_unbound_or_mismatched_history(historical):
    old, _, fresh = vegetables()
    with pytest.raises(ValueError, match="historical recipe"):
        probe_history_rotation(
            [old],
            [fresh],
            Constraints(dish_count=1),
            RuleEngine(),
            [[old.name]],
            ranking="declared_content",
            history_menus=historical,
        )


@pytest.mark.parametrize("mode", ["local", "continue", "negative"])
def test_content_objective_does_not_expand_scope_or_interpret_negative_preferences(mode):
    old, _, fresh = vegetables()
    kwargs = {}
    if mode == "local":
        kwargs["replace_slot"] = 1
    elif mode == "continue":
        kwargs["recheck_soft_preferences"] = False
    else:
        kwargs["constraints"] = Constraints(dish_count=1, preferences=["不要便当"])
    result = probe([old], [fresh], [[old]], **kwargs)
    assert result.recipes == [old] and not result.exchanges


@pytest.mark.parametrize("scene", ["便当", "家庭聚餐"])
def test_content_gain_cannot_pay_for_lost_slot_scene(scene):
    old = tagged("蒸白菜", "白菜100克", [scene])
    # Equal aggregate relevance reaches the specific scene guard; otherwise
    # the earlier relevance filter alone rejects this proposal.
    fresh = tagged("蒸西兰花", "西兰花100克", ["清淡"])
    result = probe(
        [old],
        [fresh],
        [[old]],
        constraints=Constraints(people=2, dish_count=1, preferences=[scene, "清淡"]),
    )
    assert result.recipes == [old]
    assert result.blockers["scene_reference_pool_rejected"] > 0


@pytest.mark.parametrize("danger", ["花生100克", "辣椒100克"])
def test_content_features_cannot_bypass_allergy_or_nonspicy(danger):
    old, _, fresh = vegetables()
    fresh.ingredients.append(Ingredient(name=danger[:2], raw=danger))
    fresh.raw_ingredients += "；" + danger
    result = probe(
        [old],
        [fresh],
        [[old]],
        constraints=Constraints(dish_count=1, no_spicy=True, allergies=["花生"]),
    )
    assert result.recipes == [old]


def test_content_gain_cannot_drop_explicit_preferred_food():
    old, _, fresh = vegetables()
    result = probe(
        [old],
        [fresh],
        [[old]],
        constraints=Constraints(dish_count=1, preferred_ingredients=["白菜"]),
    )
    assert result.recipes == [old]


def test_budget_exhaustion_retains_truthful_status():
    old, renamed, fresh = vegetables()
    result = probe([old], [renamed, fresh], [[old]], evaluation_limit=1)
    assert result.status == "budget_exhausted_best_observed_NOT_optimal"
    assert result.evaluated == 1


def test_observation_history_recency_limit_and_name_deduplication():
    old, _, _ = vegetables()
    exposure = history_food_exposure(old, [[old, old]] * 9)
    assert exposure.observed_pairs == 8 and exposure.missing_pairs == 0
    assert exposure.weighted_cosine_units == sum(range(1, 9)) * COSINE_UNIT


def test_different_roles_are_not_history_content_pairs():
    old, _, fresh = vegetables()
    fresh.categories = ["protein"]
    exposure = history_food_exposure(old, [[fresh]])
    assert exposure.observed_pairs == exposure.missing_pairs == 0
    assert exposure.weighted_cosine_units is None


def test_partial_features_have_finite_cosine_not_nutrient_equivalence():
    old = record("鸡肉蒸菜", "鸡胸肉100克", "蒸熟。")
    past = record("鸡肉配玉米", "鸡胸肉100克；玉米粒100克", "蒸熟。")
    old.categories = past.categories = ["protein"]
    exposure = history_food_exposure(old, [[past]])
    assert exposure.weighted_cosine_units == round(COSINE_UNIT * 2**-0.5)


def test_source_and_history_are_not_mutated_and_result_is_repeatable():
    old, renamed, fresh = vegetables()
    snapshots = [r.model_dump_json() for r in (old, renamed, fresh)]
    a = probe([old], [renamed, fresh], [[old]])
    b = probe([old], [renamed, fresh], [[old]])
    assert a == b
    assert snapshots == [r.model_dump_json() for r in (old, renamed, fresh)]
