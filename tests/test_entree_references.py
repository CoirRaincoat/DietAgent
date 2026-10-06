"""Authored source references; no serving masses or independently labelled quality."""

import pytest

from app.domain.entree_preferences import (
    entree_reference_mask,
    entree_request_clauses,
    meat_entree_reference,
    mixed_entree_active,
    mixed_entree_coverage,
)
from app.domain.matching_tags import flavor_preference_issues
from app.domain.method_preferences import method_request_clauses, method_requests
from app.domain.models import Constraints
from tests.test_component_slots import dish


@pytest.mark.parametrize(
    "name,foods",
    [
        ("清蒸鸡肉", "鸡胸肉200克"),
        ("白切肘子", "猪肘子200克"),
        ("蛋黄蒸肉", "肉末200克；蛋黄20克"),
        ("清蒸鲈鱼", "鲈鱼200克"),
        ("蒸虾仁", "虾仁200克"),
        ("清蒸牛排", "牛排200克"),
    ],
)
def test_named_whole_meat_body_has_a_reference_not_a_serving_claim(name, foods):
    recipe = dish(name, foods, "将食材蒸熟后装盘。")
    assert meat_entree_reference(recipe) and entree_reference_mask(recipe) == 1


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("蒸鸡蛋", "鸡蛋200克", "鸡蛋蒸熟后装盘。"),
        ("虾皮鸡蛋羹", "鸡蛋200克；虾皮5克", "鸡蛋虾皮蒸熟后食用。"),
        ("鸡汤豆腐", "豆腐200克；鸡汤300毫升", "豆腐鸡汤煮熟后食用。"),
        ("猪油蒸蛋", "鸡蛋200克；猪油5克", "鸡蛋猪油蒸熟后装盘。"),
        ("惠灵顿牛排", "冷冻惠灵顿牛排200克；蛋黄10克", "牛排解冻后刷蛋黄液。"),
        ("鸡肉饭", "鸡肉200克；大米200克", "鸡肉大米加水蒸熟后食用。"),
        ("鸡肉汤", "鸡肉200克；水500毫升", "鸡肉加水煮汤后食用。"),
        ("蒸豆腐", "豆腐200克", "豆腐蒸熟后装盘。"),
    ],
)
def test_egg_garnish_sauce_prepared_product_soup_staple_never_fill_meat_body(name, foods, steps):
    assert not meat_entree_reference(dish(name, foods, steps))


def test_forged_cached_protein_role_cannot_create_a_source_meat_body():
    recipe = dish("鸡肉饭", "鸡肉200克；大米200克", "鸡肉大米加水蒸熟后食用。")
    assert not meat_entree_reference(recipe.model_copy(update={"categories": ["protein"]}))


def test_meat_source_does_not_disappear_into_a_vegetarian_bit():
    source = dish("虾皮蒸白菜", "白菜200克；虾皮5克", "白菜虾皮蒸熟后装盘。")
    assert entree_reference_mask(source) == 0
    unknown = dish("复合酱蒸白菜", "白菜200克；复合酱5克", "白菜复合酱蒸熟后装盘。")
    assert entree_reference_mask(unknown) == 0
    egg = dish("蒸鸡蛋", "鸡蛋200克", "鸡蛋蒸熟后装盘。")
    assert entree_reference_mask(egg) == 2 and mixed_entree_coverage([egg, source]) == 2


def test_cached_reference_follows_source_labels_steps_roles_and_eligibility_not_identity():
    source = dish("蒸白菜", "白菜200克", "白菜蒸熟后装盘。")
    assert entree_reference_mask(source) == 2
    assert entree_reference_mask(source.model_copy(update={"steps": "白菜加鸡汤煮熟后食用。"})) == 0
    assert entree_reference_mask(source.model_copy(update={"raw_label": "甜品"})) == 0
    assert entree_reference_mask(source.model_copy(update={"categories": ["protein"]})) == 0
    assert entree_reference_mask(source.model_copy(update={"eligible": False})) == 0
    assert (
        entree_reference_mask(source.model_copy(update={"quality_flags": ["unparsed_ingredients"]}))
        == 0
    )


@pytest.mark.parametrize(
    "text", ["注意荤素和做法多样", "荤素搭配与做法多样性", "希望荤素均衡及做法丰富"]
)
def test_joint_literal_request_reaches_both_independent_executors(text):
    assert entree_request_clauses(text) == ("荤素搭配",)
    assert method_request_clauses(text) == ("做法多样",)
    assert method_requests([text]).diversity
    assert not flavor_preference_issues([text])


@pytest.mark.parametrize(
    "text",
    [
        "是否要荤素搭配？",
        "解释荤素搭配",
        "比如荤素和做法多样",
        "“注意荤素和做法多样”",
        "如果要荤素搭配",
    ],
)
def test_questions_examples_and_quotations_never_create_mixed_edit_authority(text):
    assert not entree_request_clauses(text)


@pytest.mark.parametrize(
    "fields",
    [
        {"people": 1, "dish_count": 6, "meal_type": "晚餐"},
        {"people": 5, "dish_count": 4, "meal_type": "晚餐"},
        {"people": 5, "dish_count": 6, "meal_type": "早餐"},
        {"people": 5, "dish_count": 6, "soup_count": 2, "meal_type": "晚餐"},
    ],
)
def test_shared_default_has_bounded_context_not_every_menu(fields):
    assert not mixed_entree_active(Constraints(**fields))
    assert mixed_entree_active(Constraints(**fields, preferences=["荤素搭配"]))


@pytest.mark.parametrize(
    "fields",
    [
        {"diet_mode": "vegan"},
        {"diet_mode": "ovo_lacto_vegetarian"},
        {"meat_dish_count": 0},
        {"vegetarian_dish_count": 0},
        {"preferences": ["不要荤素搭配"]},
    ],
)
def test_diet_zero_quota_and_negative_request_override_shared_default(fields):
    assert not mixed_entree_active(Constraints(people=5, dish_count=6, meal_type="晚餐", **fields))
