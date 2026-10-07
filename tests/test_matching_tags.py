"""Independent public source contrasts for descriptive matching, not paid calls."""

from dataclasses import FrozenInstanceError

import pytest

from app.domain.matching_tags import (
    flavor_coverage,
    flavor_preference_issues,
    flavor_strength,
    matching_tags,
    supported_flavor_preferences,
)
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def recipe(name="蒸白菜", foods="白菜；盐；水", steps="白菜加水蒸熟。", label=""):
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": label,
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "raw,cached,expected",
    [
        ("晚餐、午餐", ["早餐"], ("午餐", "晚餐")),
        ("护心", ["早餐"], ()),
        ("", ["早餐", "未知餐次"], ("早餐",)),
        ("适合早餐、孩子", [], ()),
    ],
)
def test_exact_meal_labels_do_not_turn_missing_data_or_prose_into_context(raw, cached, expected):
    source = recipe(label=raw).model_copy(update={"meal_types": cached})
    tags = matching_tags(source)
    assert tags.meals == expected
    assert ("meal" in tags.unknown_axes) == (not expected)


@pytest.mark.parametrize(
    "preferences,expected",
    [
        (["蒜香"], ("蒜香",)),
        (["喜欢蒜香", "酸甜", "糖醋"], ("蒜香", "酸甜")),
        (["不要甜", "不喜欢酸", "不辣", "不要蒜香"], ()),
        (["不要辣但喜欢酸甜", "不爱蒜香，不过喜欢葱香"], ("酸甜", "葱香")),
        (["不辣、蒜香", "蒜香不要"], ()),
        (["不要甜但喜欢酸"], ("酸",)),
        (["不喜欢蒜香，不过喜欢甜口"], ("甜",)),
        (["口味偏酸"], ("酸",)),
        (["喜欢五香粉"], ()),
        (["酸", "不要酸"], ()),
        (["微辣", "蒜香", "咸鲜", "香脆"], ("微辣", "蒜香", "咸鲜", "香脆")),
        (["如果可以蒜香", "解释酸甜", "是否要蒜香？"], ()),
        (["健康", "护心", "降压", "低糖", "低钠", "热量低"], ()),
        (["不甜", "不酸", "不油腻", "不太咸", "不蒜香", "不太微辣"], ()),
        (["不能吃蒜香", "蒜香也不能吃", "蒜香都忌", "不需要甜口"], ()),
        (["喜欢香菇", "想吃鲜肉", "甜玉米", "酸梅", "香菜", "鲜蔬"], ()),
        (["我喜欢甜一点", "鲜一点", "喜欢香菇和甜口"], ("甜", "鲜")),
    ],
)
def test_supported_positive_requests_are_bounded_and_exclusions_never_coverage(
    preferences, expected
):
    assert supported_flavor_preferences(preferences) == expected


@pytest.mark.parametrize(
    "preferences,marker",
    [
        (["咖喱味"], "咖喱味"),
        (["不要酸"], "不要酸"),
        (["酸", "不要酸"], "冲突"),
        (["喜欢五香粉"], "五香粉"),
    ],
)
def test_unknown_or_conflicting_reference_never_silently_passes(preferences, marker):
    assert any(marker in issue for issue in flavor_preference_issues(preferences))


def test_exact_source_flavor_beats_a_title_and_ingredient_does_not_create_a_guarantee():
    source = recipe("蒜香蒸白菜", foods="白菜；蒜末；盐", label="蒜香、晚餐")
    tags = matching_tags(source)
    assert flavor_strength(source, "喜欢蒜香") == 3
    assert tags.flavors[0].tag == "蒜香" and tags.flavors[0].origin == "source_label"
    assert tags.flavors[0].evidence == ("原始标签：蒜香",)


@pytest.mark.parametrize(
    "name,requested",
    [
        ("蒜香蒸白菜", "蒜香"),
        ("糖醋鳕鱼", "酸甜"),
        ("酸甜鳕鱼", "糖醋"),
        ("葱香豆腐", "葱香"),
        ("椒盐南瓜", "椒盐"),
    ],
)
def test_literal_title_prefix_is_lower_strength_written_reference(name, requested):
    source = recipe(name)
    assert flavor_strength(source, requested) == 2
    evidence = next(
        item for item in matching_tags(source).flavors if item.origin == "title_reference"
    )
    assert name in evidence.evidence[0]


@pytest.mark.parametrize("name", ["蒸白菜", "护心蒜香白菜", "非蒜香白菜", "蒜末蒸白菜"])
def test_garlic_presence_and_arbitrary_title_mentions_are_not_flavor_matches(name):
    source = recipe(name, foods="白菜；蒜末；盐")
    assert flavor_strength(source, "蒜香") == 0
    assert not flavor_coverage(source, ["蒜香"])
    tags = matching_tags(source)
    assert "flavor" in tags.unknown_axes
    assert any(item.origin == "ingredient_cue" for item in tags.flavors)


def test_canonical_alias_coverage_preserves_independent_requested_flavors():
    source = recipe("糖醋鳕鱼", label="蒜香")
    assert supported_flavor_preferences(["酸甜", "糖醋", "蒜香", "甜"]) == ("酸甜", "蒜香", "甜")
    assert flavor_coverage(source, ["酸甜", "糖醋", "蒜香", "甜"]) == 0b011
    assert flavor_strength(source, "喜欢酸甜和蒜香") == 0


def test_cached_flavor_or_health_tag_cannot_be_promoted_as_source_evidence():
    source = recipe(label="护心、降压、低脂、适合家庭聚餐").model_copy(
        update={"labels": ["蒜香", "家庭聚餐"]}
    )
    tags = matching_tags(source)
    assert not tags.flavors and not tags.scenes
    assert not flavor_coverage(source, ["蒜香", "清淡"])
    assert {"flavor", "scene"} <= set(tags.unknown_axes)


@pytest.mark.parametrize(
    "name,label,scene,origin",
    [
        ("家常豆腐", "", "家常", "title_reference"),
        ("普通白菜", "家庭聚餐", "家庭聚餐", "source_label"),
        ("家庭聚餐蒸鱼", "", "家庭聚餐", "title_reference"),
        ("一人食盖饭", "", "一人食", "title_reference"),
    ],
)
def test_only_literal_scene_references_are_exported(name, label, scene, origin):
    tags = matching_tags(recipe(name, label=label))
    assert [(item.tag, item.origin) for item in tags.scenes] == [(scene, origin)]


@pytest.mark.parametrize(
    "name,label",
    [
        ("宝宝辅食蒸蛋", "早餐"),
        ("五人份炖鸡", "晚餐"),
        ("快手蒸白菜", "午餐"),
        ("非宴客蒸鱼", ""),
        ("不适合家庭聚餐蒸鱼", ""),
    ],
)
def test_baby_portion_time_or_negated_title_does_not_infer_a_dining_scene(name, label):
    source = recipe(name, label=label)
    tags = matching_tags(source)
    assert not tags.scenes and "scene" in tags.unknown_axes


def test_source_flavor_and_title_matching_cannot_bypass_original_ingredient_hard_rules():
    source = recipe("蒜香蒸鱼", foods="鱼；蒜末；辣椒；盐", label="清淡、蒜香、晚餐")
    rules = RuleEngine()
    assert flavor_coverage(source, ["蒜香", "清淡"]) == 0b11
    assert not rules.evaluate(source, Constraints(no_spicy=True)).allowed
    assert not rules.evaluate(source, Constraints(allergies=["鱼"])).allowed
    assert source.raw_ingredients == "鱼；蒜末；辣椒；盐"


def test_source_object_identity_hash_inputs_and_categories_are_untouched_and_tags_frozen():
    source = recipe("家常蒜香白菜", foods="白菜；蒜末；盐", label="蒜香、午餐")
    before = source.model_dump_json()
    tags = matching_tags(source)
    assert tags.culinary_role == tuple(source.categories)
    assert before == source.model_dump_json()
    assert isinstance(tags.meals, tuple) and isinstance(tags.flavors, tuple)
    with pytest.raises(FrozenInstanceError):
        tags.meals = ("早餐",)
    with pytest.raises(FrozenInstanceError):
        tags.flavors[0].tag = "护心"


@pytest.mark.parametrize("holiday", ["圣诞节", "复活节"])
def test_holidays_are_literal_source_label_references_not_title_or_population_inferences(holiday):
    source = recipe("普通白菜", label=holiday)
    tags = matching_tags(source)
    assert [(item.tag, item.origin) for item in tags.scenes] == [(holiday, "source_label")]
    assert not matching_tags(recipe(holiday + "白菜")).scenes
    assert not matching_tags(recipe("普通白菜", label="适合" + holiday + "聚餐")).scenes
    assert "scene" in matching_tags(recipe(holiday + "白菜")).unknown_axes
