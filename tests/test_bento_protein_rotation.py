"""One read-source bento protein alternative, not a storage/safety certificate."""

import pytest

from app.agent.next_meal_rotation import repair_next_meal_repetition
from app.domain.matching_tags import scene_reference_mask
from app.domain.models import Constraints
from app.domain.scene_references import scene_reference
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog


def test_source_sliced_steamed_fish_has_only_a_separate_bento_reference():
    fish = catalog()[150]
    before = fish.model_dump_json()
    reference = scene_reference(fish)
    assert reference is not None and reference.scenes == ("便当",)
    assert reference.origin == "assistant_review_reference"
    assert reference.ingredient_excerpt in fish.raw_ingredients
    assert reference.preparation_excerpt in fish.steps
    assert scene_reference_mask(fish, ["便当", "家庭聚餐"]) == 1
    assert before == fish.model_dump_json()
    assert "蛋清20克" in fish.raw_ingredients and "生抽10毫升" in fish.raw_ingredients
    assert "腌制20分钟" in fish.steps and "按屏幕提示操作" in fish.steps


def test_authorized_next_bento_can_replace_repeated_chicken_without_soft_regression():
    old, fish = catalog()[469], catalog()[150]
    rules = RuleEngine()
    constraints = Constraints(people=1, dish_count=1, soup_count=0, meal_type="午餐",
                              preferences=["便当", "清淡"], no_spicy=True)
    assert rules.evaluate(fish, constraints).allowed
    scores = {r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [old, fish]}
    assert scores[fish.recipe_id] == scores[old.recipe_id]
    result = repair_next_meal_repetition(
        [old], [fish], constraints, recent_recipe_names=[[old.name]],
        order={fish.recipe_id: 0},
        relevance=lambda r: recipe_relevance_score(r, [], constraints, rules),
        goal_scores=scores, goal_evidence=rules.goal_evidence,
        food_matches=lambda r, term: bool(rules.food_matches(r, term)),
    )
    assert result.recipes == [fish] and result.changed_indices == {0}


@pytest.mark.parametrize("allergy", ["鱼", "鸡蛋", "芝麻"])
def test_bento_reference_does_not_waive_declared_fish_egg_or_sesame(allergy):
    assert not RuleEngine().evaluate(catalog()[150], Constraints(allergies=[allergy])).allowed


@pytest.mark.parametrize("field,value", [("steps", "鱼片装盘。"),
                                         ("raw_ingredients", "龙利鱼400克"),
                                         ("raw_label", "午餐、辣"),
                                         ("categories", ["staple"])])
def test_any_source_edit_expires_sliced_fish_reference(field, value):
    assert scene_reference(catalog()[150].model_copy(update={field: value})) is None
