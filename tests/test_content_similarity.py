"""Public feature-observation contracts, not independent preference labels."""

import pytest

from app.domain.models import Ingredient
from evaluation.content_similarity import compare_content_similarity, observe_content_similarity
from tests.test_source_health_replay_roles import record


def test_different_names_with_same_declared_food_do_not_manufacture_food_novelty():
    a = record("鸡肉蒸菜", "鸡胸肉200克；胡萝卜100克", "食材蒸熟装盘。")
    b = record("另一种餐盒鸡肉", "鸡胸肉100克；胡萝卜20克", "食材蒸熟装盘。")
    a.categories = b.categories = ["protein"]
    result = observe_content_similarity([[a], [b]])
    assert result.repeated_name_pairs == 0
    assert result.mean_declared_family_cosine == pytest.approx(1)
    assert result.observed_family_pairs == 1


def test_changing_title_does_not_change_declared_food_similarity():
    a = record("鸡肉蒸菜", "鸡胸肉200克", "鸡肉蒸熟装盘。")
    b = a.model_copy(update={"name": "本餐另一道"})
    same = observe_content_similarity([[a], [a]])
    renamed = observe_content_similarity([[a], [b]])
    assert same.mean_declared_family_cosine == renamed.mean_declared_family_cosine == 1
    assert same.repeated_name_pairs == 1 and renamed.repeated_name_pairs == 0


def test_finite_features_compare_overlap_not_portion_or_health():
    a = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    b = record("鸡肉蒸胡萝卜", "鸡胸肉200克；胡萝卜100克", "食材蒸熟装盘。")
    a.categories = b.categories = ["protein"]
    result = observe_content_similarity([[a], [b]])
    assert result.mean_declared_family_cosine == pytest.approx(2**-0.5)
    assert "不是" in result.interpretation


@pytest.mark.parametrize("foods", ["紫苏100克", "水100克", "鸡汤100克", "胡萝卜汁100克"])
def test_missing_finite_families_are_unknown_not_perfect_novelty(foods):
    a = record("已知主料", "鸡胸肉200克", "食材蒸熟装盘。")
    b = record("未知有限家族", foods, "食材煮熟装盘。")
    a.categories = b.categories = ["protein"]
    result = observe_content_similarity([[a], [b]])
    assert result.mean_declared_family_cosine is None
    assert result.observed_family_pairs == 0 and result.missing_family_pairs == 1
    assert result.missing_declared_family_occurrences == 1


@pytest.mark.parametrize("menus", [[], [[]]])
def test_empty_sequences_are_not_given_a_diversity_score(menus):
    result = observe_content_similarity(menus)
    assert result.mean_declared_family_cosine is None and result.dish_occurrences == 0


def test_one_meal_has_no_cross_meal_observation():
    a = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    result = observe_content_similarity([[a, a]])
    assert result.mean_declared_family_cosine is None
    assert result.repeated_name_pairs == 1


@pytest.mark.parametrize("roles", [[], ["staple"], ["protein", "vegetable"]])
def test_unknown_or_different_role_is_not_a_comparable_pair(roles):
    a = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    a.categories = ["protein"]
    b = a.model_copy(update={"categories": roles})
    result = observe_content_similarity([[a], [b]])
    assert result.same_role_cross_meal_pairs == 0 and result.mean_declared_family_cosine is None


def test_source_parsed_food_edit_is_recomputed_without_mutating_recipe():
    a = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    b = a.model_copy(deep=True)
    snapshot = a.model_dump_json()
    assert observe_content_similarity([[a], [b]]).mean_declared_family_cosine == 1
    b.ingredients = [Ingredient(name="猪肉", raw="猪肉200克")]
    assert observe_content_similarity([[a], [b]]).mean_declared_family_cosine == 0
    assert a.model_dump_json() == snapshot


def test_three_repeated_meals_count_all_distinct_event_pairs():
    a = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    result = observe_content_similarity([[a], [a], [a]])
    assert result.repeated_name_pairs == 3
    assert result.same_role_cross_meal_pairs == result.observed_family_pairs == 3
    assert result.mean_declared_family_cosine == 1


def test_dropping_known_features_cannot_manufacture_an_aligned_similarity_gain():
    chicken = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    pork = record("猪肉蒸菜", "猪肉200克", "食材蒸熟装盘。")
    unknown = record("紫苏配菜", "紫苏100克", "食材蒸熟装盘。")
    chicken.categories = pork.categories = unknown.categories = ["protein"]
    result = compare_content_similarity(
        [[chicken], [chicken], [pork]], [[chicken], [unknown], [pork]]
    )
    assert result.before.mean_declared_family_cosine == pytest.approx(1 / 3)
    assert result.after.mean_declared_family_cosine == 0
    assert result.before_common_mean_cosine == result.after_common_mean_cosine == 0
    assert result.common_observed_family_pairs == 1
    assert result.unobserved_in_either_pairs == 2


@pytest.mark.parametrize("after", [[], [[]], [[], []]])
def test_unaligned_comparison_refuses_to_make_a_false_pair(after):
    chicken = record("鸡肉蒸菜", "鸡胸肉200克", "食材蒸熟装盘。")
    with pytest.raises(ValueError, match="aligned"):
        compare_content_similarity([[chicken], [chicken]], after)


def test_both_unknown_comparison_has_no_novelty_number():
    unknown = record("紫苏配菜", "紫苏100克", "食材蒸熟装盘。")
    unknown.categories = ["vegetable"]
    result = compare_content_similarity([[unknown], [unknown]], [[unknown], [unknown]])
    assert result.common_observed_family_pairs == 0
    assert result.before_common_mean_cosine is None and result.after_common_mean_cosine is None
