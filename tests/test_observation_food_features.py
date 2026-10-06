"""Public finite contracts; source replay remains local and separately reported."""

import pytest

from app.domain.food_variety import food_families
from app.domain.models import Ingredient
from evaluation.content_similarity import compare_content_similarity, observe_content_similarity
from evaluation.declared_food_features import (
    OBSERVATION_FAMILY_VERSION,
    observation_food_families,
)
from tests.test_source_health_replay_roles import record


@pytest.mark.parametrize(
    "name,family",
    [
        ("芥蓝", "chinese_kale"),
        ("秋葵", "okra"),
        ("蚕豆", "broad_bean"),
        ("鲜蚕豆", "broad_bean"),
        ("韭菜", "chive"),
        ("香菇", "shiitake"),
        ("干香菇", "shiitake"),
        ("水发香菇", "shiitake"),
        ("芹菜", "celery"),
        ("西芹", "celery"),
        ("虾仁", "shrimp"),
        ("大黄鱼", "yellow_croaker"),
        ("玉米粒", "corn"),
        ("新鲜玉米", "corn"),
        ("白萝卜", "white_radish"),
    ],
)
def test_finite_declared_food_observation_without_changing_planner(name, family):
    sample = record("普通装盘", "水100克", "蒸熟。")
    sample.ingredients = [Ingredient(name=name, raw=name)]
    snapshot = sample.model_dump_json()
    assert observation_food_families(sample) == {family}
    assert food_families(sample) == frozenset()
    assert sample.model_dump_json() == snapshot


@pytest.mark.parametrize(
    "name",
    [
        "秋葵粉",
        "秋葵提取物",
        "香菇酱",
        "香菇汁",
        "香菇精",
        "香菇调味粉",
        "芹菜汁",
        "芥蓝粉",
        "蚕豆酱",
        "韭菜花酱",
        "虾米",
        "虾油",
        "虾酱",
        "黄鱼汤",
        "鱼露",
        "蒸鱼豉油",
        "玉米淀粉",
        "玉米油",
        "白萝卜汁",
        "肉末",
        "里脊肉",
        "蔬菜碎",
        "虾仁味调料",
    ],
)
def test_no_substrings_or_guessed_animals_extracts_sauces(name):
    sample = record("芥蓝秋葵香菇虾仁玉米", "水100克", "加入虾仁蒸熟。")
    sample.ingredients = [Ingredient(name=name, raw=name)]
    assert observation_food_families(sample) == frozenset()


def test_optional_policy_observes_missed_pair_but_never_claims_novelty_from_missing():
    kale = record("蒸芥蓝", "芥蓝100克", "芥蓝蒸熟。")
    okra = record("蒸秋葵", "秋葵100克", "秋葵蒸熟。")
    kale.categories = okra.categories = ["vegetable"]
    old = observe_content_similarity([[kale], [okra]])
    new = observe_content_similarity([[kale], [okra]], feature_policy="observation_v1")
    assert old.mean_declared_family_cosine is None
    assert new.mean_declared_family_cosine == 0
    assert new.family_policy_version == OBSERVATION_FAMILY_VERSION
    assert new.observed_family_pairs == 1


def test_new_policy_still_does_not_credit_renaming_or_unknown_food():
    kale = record("蒸芥蓝", "芥蓝100克", "芥蓝蒸熟。")
    renamed = kale.model_copy(update={"name": "另一道菜单名字"})
    unknown = record("普通配菜", "蔬菜碎100克", "蒸熟。")
    kale.categories = renamed.categories = unknown.categories = ["vegetable"]
    observed = observe_content_similarity([[kale], [renamed]], feature_policy="observation_v1")
    assert observed.mean_declared_family_cosine == 1 and observed.repeated_name_pairs == 0
    missing = observe_content_similarity([[kale], [unknown]], feature_policy="observation_v1")
    assert missing.mean_declared_family_cosine is None and missing.missing_family_pairs == 1


def test_new_policy_preserves_aligned_common_denominator():
    kale = record("蒸芥蓝", "芥蓝100克", "蒸熟。")
    okra = record("蒸秋葵", "秋葵100克", "蒸熟。")
    unknown = record("普通配菜", "蔬菜碎100克", "蒸熟。")
    kale.categories = okra.categories = unknown.categories = ["vegetable"]
    result = compare_content_similarity(
        [[kale], [kale], [okra]], [[kale], [unknown], [okra]], feature_policy="observation_v1"
    )
    assert result.before.mean_declared_family_cosine == pytest.approx(1 / 3)
    assert result.before_common_mean_cosine == result.after_common_mean_cosine == 0
    assert result.common_observed_family_pairs == 1 and result.unobserved_in_either_pairs == 2


def test_recipe_edit_reobserves_declared_names_and_preserves_base_features():
    sample = record("普通蒸菜", "鸡胸肉100克；秋葵100克", "蒸熟。")
    assert observation_food_families(sample) == {"chicken", "okra"}
    sample.ingredients = [Ingredient(name="芥蓝", raw="芥蓝100克")]
    assert observation_food_families(sample) == {"chinese_kale"}


@pytest.mark.parametrize("empty", [[], [[]]])
def test_invalid_policy_rejected_even_for_empty_input(empty):
    with pytest.raises(ValueError, match="feature policy"):
        observe_content_similarity(empty, feature_policy="not_a_policy")
    with pytest.raises(ValueError, match="feature policy"):
        compare_content_similarity(empty, empty, feature_policy="not_a_policy")
