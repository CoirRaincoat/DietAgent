"""Accepted-source vocabulary contract and authored 香辣 request contrasts."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.matching_tags import (
    flavor_conflicts,
    flavor_coverage,
    flavor_exclusion_hits,
    flavor_strength,
    matching_tags,
    supported_flavor_exclusions,
    supported_flavor_preferences,
)
from app.domain.models import Constraints
from app.infrastructure.data import FLAVOR_LABELS
from app.rules.engine import RuleEngine
from tests.test_negative_flavor import recipe


@pytest.mark.parametrize("tag", sorted(FLAVOR_LABELS))
def test_every_accepted_flavor_label_has_a_matching_consumer(tag):
    source = recipe("蒸白菜", label="晚餐、" + tag)
    assert supported_flavor_preferences([tag])
    assert flavor_strength(source, tag) == 3
    assert flavor_coverage(source, [tag]) == 1


@pytest.mark.parametrize("preference", ["香辣", "喜欢香辣", "口味偏香辣"])
def test_positive_compound_reference_is_recognized_as_one_flavor(preference):
    source = recipe("蒸白菜", label="晚餐、香辣")
    assert supported_flavor_preferences([preference]) == ("香辣",)
    assert flavor_strength(source, preference) == 3
    assert (
        next(t for t in matching_tags(source).flavors if t.tag == "香辣").origin == "source_label"
    )


@pytest.mark.parametrize("preference", ["不要香辣", "不喜欢辣", "不要香"])
def test_negative_compound_or_component_excludes_explicit_source(preference):
    source = recipe("蒸白菜", label="晚餐、香辣")
    assert supported_flavor_exclusions([preference])
    assert flavor_exclusion_hits(source, [preference])
    assert not RuleEngine().evaluate(source, Constraints(preferences=[preference])).allowed


def test_compound_exclusion_is_directional_not_a_universal_spicy_ban():
    plain_spicy = recipe("蒸白菜", label="晚餐、辣")
    assert not flavor_exclusion_hits(plain_spicy, ["不要香辣"])
    assert not flavor_conflicts(["辣", "不要香辣"])
    assert flavor_conflicts(["香辣", "不要辣"]) == ("香辣",)


def test_compound_positive_does_not_change_existing_exact_positive_contract():
    source = recipe("蒸白菜", label="晚餐、香辣")
    assert flavor_strength(source, "辣") == 0
    assert flavor_coverage(source, ["辣", "香", "香辣"]) == 0b100
    components = recipe("蒸白菜", label="晚餐、香、辣")
    assert not flavor_coverage(components, ["香辣"])


def test_unknown_ingredient_and_title_mentions_do_not_create_new_source_labels():
    source = recipe("香辣白菜", label="晚餐", foods="白菜200克；辣椒20克")
    assert flavor_strength(source, "香辣") == 0
    assert not flavor_exclusion_hits(source, ["不要香辣"])
    assert not RuleEngine().evaluate(source, Constraints(no_spicy=True)).allowed


def test_initial_and_authorized_new_requirement_replace_known_excluded_source():
    old = recipe("蒸白菜甲", label="晚餐、香辣")
    neutral = recipe("蒸白菜乙", label="晚餐、原味")
    c = Constraints(dish_count=1, preferences=["不要香辣"])
    result = MenuPlanner(RuleEngine()).plan([old, neutral], c, current=[old])
    assert result.failure is None and result.recipes == [neutral]


def test_exclusion_outside_local_slot_never_silently_changes_other_slot():
    old = recipe("蒸白菜甲", label="晚餐、香辣")
    chicken = recipe("蒸鸡肉", foods="鸡肉200克", steps="蒸熟装盘。")
    alternative = recipe("煮鸡肉", foods="鸡肉200克", steps="煮熟装盘。")
    neutral = recipe("蒸白菜乙", label="晚餐、原味")
    result = MenuPlanner(RuleEngine()).plan(
        [old, chicken, alternative, neutral],
        Constraints(dish_count=2, preferences=["不要香辣"]),
        current=[old, chicken],
        replace_slot=2,
    )
    assert result.failure and "其他菜位" in result.failure and not result.recipes


def test_nonspicy_and_allergy_are_never_paid_for_by_new_matching_credit():
    spicy = recipe("蒸白菜", label="晚餐、清淡、香辣")
    assert (
        not RuleEngine().evaluate(spicy, Constraints(no_spicy=True, preferences=["香辣"])).allowed
    )
    peanut = recipe("蒸鸡肉", label="晚餐、香辣", foods="鸡肉200克；花生20克")
    assert (
        not RuleEngine()
        .evaluate(peanut, Constraints(allergies=["花生"], preferences=["香辣"]))
        .allowed
    )
