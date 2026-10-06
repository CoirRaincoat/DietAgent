"""Authored uncertainty controls, not nutrition or independent quality labels."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine
from evaluation.content_refinement_probe import probe_content_refinement
from evaluation.history_food_exposure import history_food_exposure
from evaluation.history_saturation_probe import probe_history_rotation
from tests.test_source_health_replay_roles import record


def pair(kind: str) -> tuple[Recipe, Recipe]:
    if kind == "vegetable":
        old = record("蒸白菜", "白菜100克", "白菜蒸熟装盘。")
        new = old.model_copy(deep=True, update={"recipe_id": "unknown", "name": "另一份蒸菜"})
        new.ingredients = [Ingredient(name="蔬菜碎", raw="蔬菜碎100克")]
        new.raw_ingredients = "蔬菜碎100克"
    else:
        old = record("清蒸鸡胸", "鸡胸肉100克", "鸡胸肉蒸熟装盘。")
        if kind == "animal":
            new = record("蒸肉末", "肉末100克", "肉末蒸熟装盘。")
        else:
            new = record("蒸牛肉配胡萝卜", "牛肉100克；胡萝卜100克", "牛肉和胡萝卜蒸熟。")
        assert old.categories == new.categories == ["protein"]
    return old, new


@pytest.mark.parametrize("kind", ["vegetable", "animal", "protein_gap"])
def test_two_stage_name_rotation_does_not_lose_finite_evidence(kind: str) -> None:
    old, new = pair(kind)
    c = Constraints(dish_count=1)
    legacy = probe_content_refinement(
        [old], [new], c, RuleEngine(), [[old.name]], [[old]], preserve_name_evidence=False
    )
    assert legacy.baseline.recipes == legacy.recipes == [new]
    protected = probe_content_refinement([old], [new], c, RuleEngine(), [[old.name]], [[old]])
    assert protected.baseline.recipes == protected.recipes == [old]
    assert any("name_evidence" in key for key in protected.baseline.blockers)


@pytest.mark.parametrize("kind", ["animal", "protein_gap"])
def test_unknown_history_cannot_hide_loss_of_current_identity(kind: str) -> None:
    old, new = pair(kind)
    past = old.model_copy(deep=True)
    past.ingredients = [Ingredient(name="肉末", raw="肉末100克")]
    before = history_food_exposure(old, [[past]])
    after = history_food_exposure(new, [[past]])
    assert before.observed_pairs == after.observed_pairs == 0
    assert before.missing_pairs == after.missing_pairs == 1
    result = probe_content_refinement(
        [old], [new], Constraints(dish_count=1), RuleEngine(), [[past.name]], [[past]]
    )
    assert result.recipes == [old]


def test_known_declared_alternative_can_rotate_instead_of_freezing_everything() -> None:
    old, _ = pair("vegetable")
    fresh = record("蒸西兰花", "西兰花100克", "西兰花蒸熟装盘。")
    result = probe_content_refinement(
        [old], [fresh], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[old]]
    )
    assert result.recipes == [fresh]


def test_guard_requires_bound_recipe_history() -> None:
    old, new = pair("vegetable")
    with pytest.raises(ValueError, match="historical recipe"):
        probe_history_rotation(
            [old],
            [new],
            Constraints(dish_count=1),
            RuleEngine(),
            [[old.name]],
            preserve_name_evidence=True,
        )


def test_staple_accompaniments_cannot_hide_unrepresented_grain_body() -> None:
    old = record("清蒸大米饭", "大米150克", "大米蒸熟装盘。")
    new = record("糯米猪肉饭", "糯米150克；猪肉100克；香菇30克", "糯米、猪肉和香菇蒸熟。")
    assert old.categories == new.categories == ["staple"]
    result = probe_content_refinement(
        [old], [new], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[old]]
    )
    assert result.recipes == [old]
    assert result.baseline.blockers["name_evidence_staple_representation_pool_rejected"] > 0


def test_content_stage_also_cannot_trade_represented_rice_for_garnish_features() -> None:
    old = record("大米猪肉饭", "大米150克；猪肉100克", "大米和猪肉蒸熟装盘。")
    renamed = old.model_copy(update={"recipe_id": "renamed-rice", "name": "另一份大米猪肉饭"})
    missing = record("糯米香菇饭", "糯米150克；香菇30克", "糯米、香菇蒸熟。")
    result = probe_content_refinement(
        [old], [renamed, missing], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[old]]
    )
    assert result.recipes == [renamed]
    assert result.refinement is not None
    assert result.refinement.blockers["name_evidence_staple_representation_pool_rejected"] > 0


def test_represented_staple_alternatives_are_not_all_frozen() -> None:
    old = record("清蒸大米饭", "大米150克", "大米蒸熟装盘。")
    fresh = record("清蒸小米饭", "小米150克", "小米蒸熟装盘。")
    result = probe_content_refinement(
        [old], [fresh], Constraints(dish_count=1), RuleEngine(), [[old.name]], [[old]]
    )
    assert result.recipes == [fresh]


@pytest.mark.parametrize("scope", ["local", "continue"])
def test_guard_never_expands_edit_or_continuation_scope(scope: str) -> None:
    old, new = pair("vegetable")
    result = probe_content_refinement(
        [old],
        [new],
        Constraints(dish_count=1),
        RuleEngine(),
        [[old.name]],
        [[old]],
        replace_slot=1 if scope == "local" else None,
        recheck_soft_preferences=scope != "continue",
    )
    assert result.recipes == [old] and result.evaluated == 0
