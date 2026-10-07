"""Assistant-authored culinary scene references; not independent suitability gold."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.scene_preferences import scene_warnings
from app.domain.matching_tags import matching_tags, scene_reference_mask
from app.domain.models import Constraints
from app.domain.scene_references import scene_reference
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish

ROWS = (37, 42, 132, 299, 314, 356, 377, 469, 502, 594, 786, 1817)
BENTO_ROWS = (37, 132, 299, 356, 377, 469, 502, 786, 1817)


@pytest.mark.parametrize("row", ROWS)
def test_read_source_record_has_separate_assistant_reference_not_original_scene(row):
    recipe = catalog()[row]
    before = recipe.model_dump_json()
    tags = matching_tags(recipe)
    references = getattr(tags, "scene_references", ())
    assert references
    assert all(item.origin == "assistant_review_reference" for item in references)
    assert {item.tag for item in references} == (
        {"家庭聚餐", "便当"} if row in BENTO_ROWS else {"家庭聚餐"}
    )
    assert not tags.scenes and "scene" in tags.unknown_axes
    assert before == recipe.model_dump_json()
    assert scene_reference_mask(recipe, ["家庭聚餐"]) == 1


def test_scene_reference_is_used_by_retrieval_and_planner_with_visible_boundary():
    old = dish("蒸白菜", "白菜200克；盐1克", "白菜蒸熟装盘。")
    reviewed = catalog()[356]
    constraints = Constraints(dish_count=1, meal_type="午餐", preferences=["便当"])
    retriever = KeywordRetriever([old, reviewed])
    assert retriever.search([], constraints, required_scenes=["便当"]) == [reviewed]
    result = MenuPlanner(RuleEngine()).plan([old, reviewed], constraints, current=[old])
    assert result.recipes == [reviewed]
    assert any("助手补充" in warning and "未经独立复核" in warning for warning in result.warnings)


@pytest.mark.parametrize("row", [314, 594])
def test_soup_and_whole_fish_are_not_promoted_as_bento_by_family_reference(row):
    recipe = catalog()[row]
    assert scene_reference_mask(recipe, ["家庭聚餐", "便当"]) == 0b01


def test_no_scene_request_does_not_add_a_scene_success_statement():
    assert not scene_warnings([catalog()[356]], Constraints())


def test_assistant_reference_never_waives_no_spicy_or_shellfish_allergy():
    assert scene_reference_mask(catalog()[377], ["便当"]) == 1
    assert not RuleEngine().evaluate(catalog()[377], Constraints(no_spicy=True)).allowed
    assert not RuleEngine().evaluate(catalog()[1817], Constraints(allergies=["虾"])).allowed


def test_unreviewed_population_label_does_not_become_bento_reference():
    recipe = dish("普通白菜", "白菜200克", "白菜蒸熟装盘。").model_copy(
        update={"raw_label": "午餐、上班族"}
    )
    assert not getattr(matching_tags(recipe), "scene_references", ())
    assert scene_reference_mask(recipe, ["便当", "家庭聚餐"]) == 0


def test_sodium_presence_does_not_veto_an_equal_score_source_scene_reference():
    old = dish("蒸白菜", "白菜200克", "白菜蒸熟装盘。")
    constraints = Constraints(dish_count=1, preferences=["便当"], health_goals=["降压"])
    result = MenuPlanner(RuleEngine()).plan([catalog()[356]], constraints, current=[old])
    assert result.recipes == [catalog()[356]]
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)
    assert RuleEngine().goal_evidence(result.recipes[0], "降压").has_attention


@pytest.mark.parametrize("row", ROWS)
def test_reference_registry_is_bound_to_the_complete_record_not_only_copied_id(row):
    recipe = catalog()[row]
    assert scene_reference(recipe) is not None
    assert (
        scene_reference(recipe.model_copy(update={"steps": recipe.steps + "改为辣椒凉拌。"}))
        is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "同名加工组件"),
        ("raw_ingredients", "调味料400克"),
        ("ingredients", []),
        ("categories", ["component"]),
        ("eligible", False),
        ("source_row", 99999),
        ("fingerprint", "copied-id"),
        ("labels", ["家庭聚餐"]),
        ("raw_label", "适合所有场景"),
        ("meal_types", ["早餐"]),
        ("methods", ["炸"]),
        ("quality_flags", ["unparsed_ingredients"]),
    ],
)
def test_parsed_fields_and_cache_or_source_changes_require_fresh_review(field, value):
    assert scene_reference(catalog()[469].model_copy(update={field: value})) is None


def test_plain_continuation_does_not_churn_an_accepted_unknown_scene_menu():
    old = dish("蒸白菜", "白菜200克；盐1克", "白菜蒸熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [catalog()[356]],
        Constraints(dish_count=1, preferences=["便当"]),
        current=[old],
        recheck_soft_preferences=False,
    )
    assert result.recipes == [old]


def test_scene_repair_only_changes_the_authorized_slot_and_leaves_missing_reference_visible():
    first = dish("蒸白菜", "白菜200克；盐1克", "白菜蒸熟装盘。")
    second = dish("煮菠菜", "菠菜200克；盐1克", "菠菜煮熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [catalog()[356]],
        Constraints(dish_count=2, preferences=["便当"]),
        current=[first, second],
        replace_slot=2,
    )
    assert result.failure is None
    assert result.recipes == [first, catalog()[356]]
    assert any("第 1 道" in warning and "缺少" in warning for warning in result.warnings)
    assert any("第 2 道" in warning and "助手补充" in warning for warning in result.warnings)
