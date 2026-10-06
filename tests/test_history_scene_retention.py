"""Authored scene-rotation regressions, not independent quality gold."""

import pytest

from app.domain.matching_tags import scene_reference_mask
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from evaluation.history_saturation_probe import probe_history_rotation
from tests.test_source_health_replay_roles import record


def tagged(name, foods, tags):
    recipe = record(name, foods, "食材蒸熟装盘。")
    recipe.labels.extend(tags)
    recipe.raw_label += "、" + "、".join(tags)
    return recipe


@pytest.mark.parametrize("policy", ["additive_and_types", "saturated_types"])
@pytest.mark.parametrize("scene", ["便当", "家庭聚餐"])
def test_other_slot_scene_and_equal_flavor_credit_cannot_hide_lost_scene(policy, scene):
    before = [
        tagged("蒸白菜", "白菜200克", [scene]),
        tagged("蒸鸡肉", "鸡胸肉200克", [scene, "清淡"]),
    ]
    fresh = tagged("蒸西兰花", "西兰花200克", ["清淡"])
    constraints = Constraints(
        people=2, dish_count=2, meal_type="午餐", preferences=[scene, "清淡"], no_spicy=True
    )
    result = probe_history_rotation(
        before,
        [*before, fresh],
        constraints,
        RuleEngine(),
        [[r.name for r in before]],
        policy=policy,
    )
    assert [scene_reference_mask(r, [scene]) for r in result.recipes] == [1, 1]
    assert result.recipes == before
    assert result.blockers["scene_reference_pool_rejected"] > 0


@pytest.mark.parametrize("policy", ["additive_and_types", "saturated_types"])
def test_scene_reference_preserving_rotation_can_still_reduce_exposure(policy):
    before = [
        tagged("蒸白菜", "白菜200克", ["便当", "清淡"]),
        tagged("蒸鸡肉", "鸡胸肉200克", ["便当", "清淡"]),
    ]
    fresh = [
        tagged("蒸西兰花", "西兰花200克", ["便当", "清淡"]),
        tagged("蒸鸭肉", "鸭肉200克", ["便当", "清淡"]),
    ]
    constraints = Constraints(dish_count=2, meal_type="午餐", preferences=["便当", "清淡"])
    result = probe_history_rotation(
        before,
        [*before, *fresh],
        constraints,
        RuleEngine(),
        [[r.name for r in before]],
        policy=policy,
    )
    assert result.recipes == fresh
    assert result.exchanges[-1]["history_cost_after"] == 0
    assert all(scene_reference_mask(r, ["便当"]) == 1 for r in result.recipes)
    for exchange in result.exchanges:
        assert exchange["scene_request_types"] == ("便当",)
        assert exchange["scene_masks_before"] == exchange["scene_masks_after"] == [1, 1]


@pytest.mark.parametrize("policy", ["additive_and_types", "saturated_types"])
def test_one_scene_type_cannot_replace_another_even_if_the_other_slot_covers_both(policy):
    before = [
        tagged("蒸白菜", "白菜200克", ["便当"]),
        tagged("蒸鸡肉", "鸡胸肉200克", ["便当", "家庭聚餐"]),
    ]
    fresh = tagged("蒸西兰花", "西兰花200克", ["家庭聚餐"])
    constraints = Constraints(people=2, dish_count=2, preferences=["便当", "家庭聚餐"])
    result = probe_history_rotation(
        before,
        [*before, fresh],
        constraints,
        RuleEngine(),
        [[r.name for r in before]],
        policy=policy,
    )
    assert result.recipes == before


@pytest.mark.parametrize("mode", ["local", "continuation", "negative", "unknown"])
def test_unsupported_or_unrequested_scene_rotation_is_not_silently_enabled(mode):
    before = [tagged("蒸白菜", "白菜200克", ["便当"])]
    fresh = tagged("蒸西兰花", "西兰花200克", ["便当"])
    constraints = Constraints(dish_count=1, preferences=["便当"])
    kwargs = {}
    if mode == "local":
        kwargs["replace_slot"] = 1
    elif mode == "continuation":
        kwargs["recheck_soft_preferences"] = False
    else:
        constraints = constraints.model_copy(
            update={"preferences": ["不要便当" if mode == "negative" else "便当隔夜保证"]}
        )
    result = probe_history_rotation(
        before, [before[0], fresh], constraints, RuleEngine(), [[before[0].name]], **kwargs
    )
    assert result.recipes == before and not result.exchanges


@pytest.mark.parametrize("policy", ["additive_and_types", "saturated_types"])
@pytest.mark.parametrize("scene", ["便当", "家庭聚餐"])
def test_actual_source_assistant_references_survive_clear_flavor_history_rotation(policy, scene):
    from app.agent.planner import MenuPlanner
    from tests.test_component_slots import catalog

    source = list(catalog().values())
    constraints = Constraints(
        people=2, dish_count=3, meal_type="午餐", no_spicy=True, preferences=[scene, "清淡"]
    )
    rules = RuleEngine()
    initial = MenuPlanner(rules).plan(source, constraints)
    assert not initial.failure
    masks = [scene_reference_mask(r, [scene]) for r in initial.recipes]
    assert any(masks), "Do not let unknown-only source menus make this retention check vacuous"
    result = probe_history_rotation(
        initial.recipes,
        source,
        constraints,
        rules,
        [[r.name for r in initial.recipes]],
        policy=policy,
        pool_limit=16,
        evaluation_limit=10000,
    )
    assert all(
        scene_reference_mask(recipe, [scene]) & mask == mask
        for recipe, mask in zip(result.recipes, masks, strict=True)
    )
    assert all(rules.evaluate(r, constraints).allowed for r in result.recipes)
