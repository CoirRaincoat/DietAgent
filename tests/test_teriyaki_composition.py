"""Named sauce needs its actual source composition, not a brand guess."""

import pytest

from app.domain.models import Constraints, Ingredient
from app.rules.engine import RuleEngine
from app.rules.sauce_composition import unresolved_sauce_evidence
from tests.test_component_slots import catalog
from tests.test_unqualified_sauce_restrictions import card


@pytest.mark.parametrize("action", ["抹上", "涂上", "刷上", "淋上", "加入"])
@pytest.mark.parametrize(
    "constraints",
    [Constraints(no_spicy=True), Constraints(allergies=["花生"]), Constraints(diet_mode="vegan")],
)
def test_unqualified_teriyaki_use_is_not_proof_of_restricted_compatibility(action, constraints):
    recipe = card(f"豆腐蒸熟后{action}少许照烧汁。")
    before = recipe.model_dump()
    decision = RuleEngine().evaluate(recipe, constraints)
    assert not decision.allowed and "成分" in "；".join(decision.reasons)
    assert "辣" not in "；".join(decision.reasons).replace("饮食限制", "")
    assert recipe.model_dump() == before


def test_teriyaki_ingredient_without_step_is_still_unknown_composition():
    recipe = card("豆腐蒸熟装盘。")
    recipe.ingredients.append(Ingredient(name="照烧汁", raw="照烧汁少许"))
    recipe.raw_ingredients += "；照烧汁少许"
    assert unresolved_sauce_evidence(recipe, RuleEngine()._known_foods)
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["花生"])).allowed


@pytest.mark.parametrize(
    "definition",
    [
        "照烧汁由生抽、白糖、水、姜组成。",
        "将生抽、白糖、水、姜调成照烧汁。",
    ],
)
def test_actual_exhaustive_self_made_teriyaki_remains_allowed(definition):
    recipe = card(definition + "豆腐蒸熟后刷上照烧汁。")
    before = recipe.model_dump()
    assert not unresolved_sauce_evidence(recipe, RuleEngine()._known_foods)
    assert RuleEngine().evaluate(
        recipe, Constraints(no_spicy=True, allergies=["花生"], diet_mode="vegan")
    ).allowed
    assert recipe.model_dump() == before


@pytest.mark.parametrize(
    "definition",
    [
        "例如：照烧汁由生抽、白糖、水、姜组成。",
        "照烧汁由生抽、白糖等组成。",
        "照烧汁由生抽、神秘粉组成。",
        "不建议将生抽、白糖调成照烧汁。",
        "酱料由生抽、白糖、水、姜组成。",
    ],
)
def test_reference_examples_partial_composition_or_other_sauce_cannot_clear_actual_use(definition):
    recipe = card(definition + "豆腐蒸熟后抹上照烧汁。")
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["花生"])).allowed


def test_later_self_made_description_does_not_resolve_earlier_teriyaki_use():
    recipe = card("豆腐刷上照烧汁。照烧汁由生抽、白糖、水、姜组成。")
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["花生"])).allowed


@pytest.mark.parametrize("food", ["花生油", "辣椒"])
def test_declared_self_made_sauce_does_not_waive_actual_allergen_or_chili(food):
    recipe = card(f"照烧汁由生抽、白糖、水、{food}组成。豆腐刷上照烧汁。")
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True, allergies=["花生"])).allowed


@pytest.mark.parametrize("action", ["不抹上", "不要刷上", "无需涂上"])
def test_literal_omission_is_not_an_actual_unknown_sauce_use(action):
    assert not unresolved_sauce_evidence(
        card(f"豆腐蒸熟后{action}照烧汁。"), RuleEngine()._known_foods
    )


def test_no_restrictions_retain_teriyaki_with_composition_warning_not_spicy_claim():
    recipe = card("豆腐刷上照烧汁。")
    decision = RuleEngine().evaluate(recipe, Constraints())
    assert decision.allowed and any("成分" in warning for warning in decision.warnings)
    assert not any("高钠" in warning or "辣酱" in warning for warning in decision.warnings)


def test_actual_source_teriyaki_fish_is_not_silently_rewritten_or_brand_certified():
    recipe = next(r for r in catalog().values() if r.name == "杂蔬烤照烧龙利鱼")
    assert "照烧汁少许" in recipe.raw_ingredients and "抹上照烧汁" in recipe.steps
    before = recipe.model_dump()
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True, allergies=["花生"])).allowed
    assert recipe.model_dump() == before
