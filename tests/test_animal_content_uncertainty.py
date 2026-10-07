"""Authored declaration gaps and source-backed ranking controls, not quality gold."""

import pytest

from app.domain.food_variety import food_families
from app.domain.models import Constraints, Ingredient
from app.rules.engine import RuleEngine
from evaluation.animal_source_uncertainty import unspecified_animal_declarations
from evaluation.declared_food_features import observation_food_families
from evaluation.history_food_exposure import EXPOSURE_VERSION, history_food_exposure
from evaluation.history_saturation_probe import probe_history_rotation
from tests.test_source_health_replay_roles import record


@pytest.mark.parametrize(
    "name",
    [
        "肉",
        "瘦肉",
        "肥肉",
        "咸肉",
        "肉末",
        "瘦肉末",
        "肉馅",
        "肉片",
        "肉丝",
        "肉皮",
        "里脊肉",
        "排骨",
        "熟肉末",
        "鲜肉馅",
        "生里脊肉",
    ],
)
def test_finite_declaration_uncertainties_do_not_guess_animal(name):
    recipe = record("普通配方", "水100克", "蒸熟。")
    recipe.ingredients = [Ingredient(name=name, raw=name)]
    snapshot = recipe.model_dump_json()
    assert unspecified_animal_declarations(recipe) == {name}
    assert food_families(recipe) == observation_food_families(recipe) == frozenset()
    assert recipe.model_dump_json() == snapshot


@pytest.mark.parametrize(
    "name",
    [
        "猪肉末",
        "牛肉",
        "鸡胸肉",
        "猪里脊肉",
        "猪排骨",
        "素肉末",
        "肉末味调味粉",
        "肉桂",
        "香菇",
        "蛋清",
        "蚝油",
    ],
)
def test_no_arbitrary_substring_identity_inference(name):
    recipe = record("猪肉末配方", "水100克", "猪肉蒸熟。")
    recipe.ingredients = [Ingredient(name=name, raw=name)]
    assert unspecified_animal_declarations(recipe) == frozenset()


def test_titles_steps_and_other_explicit_animals_do_not_resolve_separate_meat_declaration():
    recipe = record("猪肉蒸蛋", "肉末100克；鸡胸肉100克；鸡蛋1个", "猪肉蒸熟。")
    assert unspecified_animal_declarations(recipe) == {"肉末"}
    assert observation_food_families(recipe) == {"chicken", "egg"}


@pytest.mark.parametrize("side", ["current", "historical", "both"])
def test_known_garnishes_cannot_make_unresolved_meat_pair_observed(side):
    unresolved = record("香菇肉丸", "肉末100克；香菇100克；芹菜100克", "肉末炒熟后蒸熟。")
    resolved = record("鸡肉丸", "鸡胸肉100克；香菇100克；芹菜100克", "鸡肉炒熟后蒸熟。")
    unresolved.categories = resolved.categories = ["protein"]
    current = unresolved if side in ("current", "both") else resolved
    past = unresolved if side in ("historical", "both") else resolved
    legacy = history_food_exposure(current, [[past]], protect_animal_identity=False)
    guarded = history_food_exposure(current, [[past]])
    assert legacy.observed_pairs == 1 and legacy.weighted_cosine_units is not None
    assert guarded.observed_pairs == 0 and guarded.weighted_cosine_units is None
    assert guarded.missing_pairs == guarded.unspecified_animal_pairs == 1
    assert guarded.evidence_policy_version == EXPOSURE_VERSION


def test_partial_history_uses_only_same_supported_pairs_without_clearing_uncertainty():
    current = record("鸡肉蒸菜", "鸡胸肉100克", "鸡肉蒸熟。")
    unknown = record("香菇肉丸", "肉末100克；香菇100克", "蒸熟。")
    current.categories = unknown.categories = ["protein"]
    result = history_food_exposure(current, [[current, unknown]])
    assert result.observed_pairs == result.missing_pairs == result.unspecified_animal_pairs == 1
    assert result.weighted_cosine_units == 1_000_000


def test_declaration_edit_rechecks_cached_uncertainty_without_rewriting_other_evidence():
    sample = record("肉丸", "肉末100克；香菇100克", "蒸熟。")
    assert unspecified_animal_declarations(sample) == {"肉末"}
    sample.ingredients = [Ingredient(name="猪肉末", raw="猪肉末100克")]
    assert not unspecified_animal_declarations(sample)


def test_animal_uncertainty_in_other_role_still_does_not_become_whole_dish_novelty():
    soup = record("萝卜肉汤", "排骨100克；白萝卜100克", "煮熟。")
    soup.categories = ["soup"]
    assert observation_food_families(soup) == {"white_radish"}
    assert history_food_exposure(soup, [[soup]]).weighted_cosine_units is None


def test_actual_source_mushroom_meatball_is_not_ranked_as_novel_from_plant_garnishes():
    from tests.test_component_slots import catalog

    source = list(catalog().values())
    ball = next(r for r in source if r.name == "香菇酿肉丸")
    assert unspecified_animal_declarations(ball) == {"肉末"}
    assert observation_food_families(ball) == {"shiitake", "celery"}
    fish = next(r for r in source if r.name == "时蔬烤黄鱼")
    assert not unspecified_animal_declarations(fish)
    result = probe_history_rotation(
        [fish],
        [ball],
        Constraints(dish_count=1),
        RuleEngine(),
        [[fish.name]],
        ranking="declared_content",
        history_menus=[[fish]],
    )
    assert result.recipes == [fish]


def test_public_mincemeat_does_not_replace_observed_animal_for_zero_content_cost():
    chicken = record("蒸鸡肉", "鸡胸肉100克", "蒸熟。")
    unknown = record("香菇肉丸", "肉末100克；香菇100克", "蒸熟。")
    chicken.categories = unknown.categories = ["protein"]
    result = probe_history_rotation(
        [chicken],
        [unknown],
        Constraints(dish_count=1),
        RuleEngine(),
        [[chicken.name]],
        ranking="declared_content",
        history_menus=[[chicken]],
    )
    assert result.recipes == [chicken]
    assert result.blockers["content_animal_identity_pool_rejected"] > 0


@pytest.mark.parametrize("animal", ["牛腩", "鸭肉"])
def test_explicit_but_unrepresented_protein_with_known_garnish_is_not_novel(animal):
    recipe = record("普通肉菜", animal + "100克；胡萝卜100克", "蒸熟。")
    recipe.categories = ["protein"]
    assert not unspecified_animal_declarations(recipe)
    assert observation_food_families(recipe) == {"carrot"}
    observed = history_food_exposure(recipe, [[recipe]])
    assert observed.weighted_cosine_units is None
    assert observed.unrepresented_protein_pairs == 1 and observed.current_protein_feature_gap


@pytest.mark.parametrize(
    "foods", ["鸡胸肉100克", "猪肉100克", "虾仁100克", "大黄鱼100克", "鸡蛋1个", "豆腐100克"]
)
def test_represented_declared_protein_sources_remain_observable_without_grams_claim(foods):
    recipe = record("普通正餐", foods + "；胡萝卜100克", "蒸熟。")
    recipe.categories = ["protein"]
    result = history_food_exposure(recipe, [[recipe]])
    assert result.observed_pairs == 1 and result.weighted_cosine_units == 1_000_000
    assert result.unrepresented_protein_pairs == 0 and not result.current_protein_feature_gap


def test_known_vegetable_with_unrepresented_animal_cannot_replace_known_protein():
    chicken = record("蒸鸡肉", "鸡胸肉100克", "蒸熟。")
    duck = record("蒸鸭肉配胡萝卜", "鸭肉100克；胡萝卜100克", "蒸熟。")
    chicken.categories = duck.categories = ["protein"]
    result = probe_history_rotation(
        [chicken],
        [duck],
        Constraints(dish_count=1),
        RuleEngine(),
        [[chicken.name]],
        ranking="declared_content",
        history_menus=[[chicken]],
    )
    assert result.recipes == [chicken]
    assert result.blockers["content_protein_feature_pool_rejected"] > 0
