"""Finite authored controls; these are not independent menu quality labels."""

import pytest

from app.domain.models import Constraints, Ingredient
from app.domain.protein_food_names import PROTEIN_FOOD_SPEC
from app.rules.engine import RuleEngine
from evaluation.content_refinement_probe import probe_content_refinement
from evaluation.declared_food_features import observation_food_families
from evaluation.history_food_exposure import history_food_exposure
from evaluation.shared_baseline_frontier import probe_shared_baseline_frontier
from tests.test_source_health_replay_roles import record


@pytest.mark.parametrize(
    "name,family",
    [
        ("牛腩", "beef"),
        ("冷冻牛腱肉片", "beef"),
        ("鸭胸肉", "duck"),
        ("乌鸡", "chicken"),
        ("河虾", "shrimp"),
        ("鲈鱼", "fish"),
        ("黄鱼", "yellow_croaker"),
        ("糯米", "rice"),
        ("圆糯米", "rice"),
        ("糙米", "rice"),
        ("藜麦", "quinoa"),
        ("薏米", "adlay"),
    ],
)
def test_explicit_shared_declarations_are_observed_without_rewriting_source(
    name: str, family: str
) -> None:
    sample = record("普通菜", "水100克", "蒸熟。")
    sample.ingredients = [Ingredient(name=name, raw=name)]
    snapshot = sample.model_dump_json()
    assert observation_food_families(sample, feature_policy="shared_v2") == {family}
    assert sample.model_dump_json() == snapshot


@pytest.mark.parametrize(
    "name",
    [
        "牛肉酱",
        "鸭油",
        "鱼露",
        "虾皮",
        "鸡精",
        "肉末",
        "素牛肉",
        "鸡腿菇",
        "糯米粉",
        "糯米酱",
        "藜麦提取物",
    ],
)
def test_compounds_and_titles_do_not_manufacture_whole_food(name: str) -> None:
    sample = record("牛肉鸭肉糯米饭", "水100克", "牛肉鸭肉糯米蒸熟。")
    sample.ingredients = [Ingredient(name=name, raw=name)]
    assert not observation_food_families(sample, feature_policy="shared_v2")


def test_fish_species_does_not_receive_duplicate_generic_fish_weight() -> None:
    sample = record("蒸鱼", "黄鱼100克", "蒸熟。")
    assert observation_food_families(sample, feature_policy="shared_v2") == {"yellow_croaker"}
    sample.ingredients.append(Ingredient(name="鲈鱼", raw="鲈鱼100克"))
    assert observation_food_families(sample, feature_policy="shared_v2") == {
        "yellow_croaker",
        "fish",
    }


@pytest.mark.parametrize("animal,family", [("牛腩", "beef"), ("鸭肉", "duck")])
def test_known_animals_are_not_garnish_only_but_legacy_replay_is_preserved(
    animal: str, family: str
) -> None:
    sample = record("蒸肉菜", animal + "100克；胡萝卜100克", "蒸熟。")
    sample.categories = ["protein"]
    old = history_food_exposure(sample, [[sample]])
    new = history_food_exposure(sample, [[sample]], feature_policy="shared_v2")
    assert old.current_protein_feature_gap and old.missing_pairs == 1
    assert new.observed_pairs == 1 and not new.current_protein_feature_gap
    assert new.weighted_cosine_units == 1_000_000
    assert family in observation_food_families(sample, feature_policy="shared_v2")
    assert old.evidence_policy_version != new.evidence_policy_version


@pytest.mark.parametrize("side", ["current", "past"])
def test_garnish_cannot_supply_unrepresented_staple_body(side: str) -> None:
    known = record("大米猪肉饭", "大米150克；猪肉100克", "蒸熟。")
    unknown = record("高粱猪肉饭", "高粱150克；猪肉100克", "蒸熟。")
    known.categories = unknown.categories = ["staple"]
    current, past = (unknown, known) if side == "current" else (known, unknown)
    result = history_food_exposure(current, [[past]], feature_policy="shared_v2")
    assert result.observed_pairs == 0 and result.weighted_cosine_units is None
    assert result.unrepresented_staple_pairs == result.missing_pairs == 1
    assert result.current_staple_feature_gap == (side == "current")


def test_generic_meat_still_prevents_novelty_even_with_new_grain_feature() -> None:
    sample = record("肉末糯米饭", "糯米150克；肉末100克", "蒸熟。")
    sample.categories = ["staple"]
    result = history_food_exposure(sample, [[sample]], feature_policy="shared_v2")
    assert result.unspecified_animal_pairs == result.missing_pairs == 1
    assert result.weighted_cosine_units is None


def test_two_stages_share_new_policy_and_allow_explicit_beef_not_generic_meat() -> None:
    old = record("清蒸鸡胸", "鸡胸肉100克", "蒸熟。")
    beef = record("清蒸牛肉", "牛肉100克", "蒸熟。")
    generic = record("清蒸肉末", "肉末100克", "蒸熟。")
    result = probe_content_refinement(
        [old],
        [generic, beef],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        [[old]],
        feature_policy="shared_v2",
    )
    assert result.recipes == [beef]
    assert result.baseline.blockers["name_evidence_animal_identity_pool_rejected"] > 0


@pytest.mark.parametrize("cap", [1, 10, 1000])
def test_shared_incumbent_uses_identical_policy_and_full_cap(cap: int) -> None:
    old = record("清蒸鸡胸", "鸡胸肉100克", "蒸熟。")
    beef = record("清蒸牛肉", "牛肉100克", "蒸熟。")
    args = ([old], [beef], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[old]])
    flat = probe_content_refinement(*args, feature_policy="shared_v2", evaluation_limit=cap)
    frontier = probe_shared_baseline_frontier(
        *args, feature_policy="shared_v2", evaluation_limit=cap
    )
    assert frontier.baseline.recipes == flat.recipes
    assert frontier.evaluated <= cap


def test_invalid_policy_is_rejected_before_empty_or_unsupported_return() -> None:
    with pytest.raises(ValueError, match="feature policy"):
        observation_food_families(record("普通菜", "水100克", "蒸熟。"), feature_policy="bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="feature policy"):
        history_food_exposure(record("普通菜", "水100克", "蒸熟。"), [], feature_policy="bad")  # type: ignore[arg-type]


def test_all_shared_whole_declarations_are_represented_without_species_double_weight() -> None:
    families = {
        "鸡蛋": "egg",
        "鱼": "fish",
        "虾": "shrimp",
        "鸡肉": "chicken",
        "猪肉": "pork",
        "牛肉": "beef",
        "鸭肉": "duck",
        "豆腐": "tofu",
    }
    for key, (_, declarations) in PROTEIN_FOOD_SPEC.items():
        for name in declarations:
            sample = record("普通菜", "水100克", "蒸熟。")
            sample.ingredients = [Ingredient(name=name, raw=name)]
            expected = "yellow_croaker" if name in {"黄鱼", "黄花鱼"} else families[key]
            assert observation_food_families(sample, feature_policy="shared_v2") == {expected}


def test_new_policy_keeps_same_rice_family_and_rechecks_edited_declarations() -> None:
    rice = record("蒸大米饭", "大米150克", "蒸熟。")
    sticky = record("蒸糯米饭", "糯米150克", "蒸熟。")
    rice.categories = sticky.categories = ["staple"]
    assert (
        history_food_exposure(sticky, [[rice]], feature_policy="shared_v2").weighted_cosine_units
        == 1_000_000
    )
    sticky.ingredients = [Ingredient(name="糯米粉", raw="糯米粉150克")]
    observed = history_food_exposure(sticky, [[rice]], feature_policy="shared_v2")
    assert observed.current_staple_feature_gap and observed.weighted_cosine_units is None
