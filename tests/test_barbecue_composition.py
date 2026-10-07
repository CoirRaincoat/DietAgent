"""Actual barbecue components need source composition, not a recipe-name guess."""

import pytest

from app.domain.models import Constraints, Ingredient
from app.rules.engine import RuleEngine
from app.rules.sauce_composition import unresolved_sauce_evidence
from tests.test_component_slots import catalog
from tests.test_unqualified_sauce_restrictions import card

NOUNS = ("烧烤粉", "烧烤汁", "烧烤酱")


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("constraints", [
    Constraints(no_spicy=True), Constraints(preferences=["不辣"]),
    Constraints(allergies=["花生"]), Constraints(excluded_ingredients=["蒜"]),
    Constraints(diet_mode="vegan"),
])
def test_bare_barbecue_declaration_cannot_pass_restricted_compatibility(noun, constraints):
    recipe = card("豆腐蒸熟装盘。")
    recipe.ingredients.append(Ingredient(name=noun, raw=noun + "10克"))
    recipe.raw_ingredients += "；" + noun + "10克"
    unchanged = recipe.model_dump()
    assert not RuleEngine().evaluate(recipe, constraints).allowed
    assert recipe.model_dump() == unchanged


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("action", ["加入", "刷上", "撒上", "撒入", "拌入"])
def test_step_only_barbecue_use_is_screened(noun, action):
    recipe = card(f"豆腐蒸熟后{action}少许{noun}。")
    assert unresolved_sauce_evidence(recipe, RuleEngine()._known_foods)
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("noun", NOUNS)
def test_source_exhaustive_self_made_barbecue_definition_is_retained(noun):
    recipe = card(f"{noun}由生抽、白糖、水、姜组成。豆腐蒸熟后加入{noun}。")
    before = recipe.model_dump()
    assert not unresolved_sauce_evidence(recipe, RuleEngine()._known_foods)
    assert RuleEngine().evaluate(recipe, Constraints(no_spicy=True, allergies=["花生"], diet_mode="vegan")).allowed
    assert recipe.model_dump() == before


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("definition", [
    "酱料由生抽、白糖、水、姜组成。",
    "例如：{noun}由生抽、白糖、水、姜组成。",
    "{noun}由生抽、白糖等组成。",
    "{noun}由生抽、未知粉组成。",
])
def test_example_partial_or_other_named_sauce_does_not_resolve_barbecue(noun, definition):
    recipe = card(definition.format(noun=noun) + f"豆腐蒸熟后刷上上述{noun}。")
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["花生"])).allowed


@pytest.mark.parametrize("noun", NOUNS)
def test_later_composition_does_not_clear_prior_barbecue_use(noun):
    recipe = card(f"豆腐加入{noun}。{noun}由生抽、白糖、水、姜组成。")
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("omission", ["不加入", "不要撒上", "无需刷上"])
def test_explicit_omission_does_not_invent_actual_barbecue_use(noun, omission):
    assert not unresolved_sauce_evidence(card(f"豆腐蒸熟后{omission}{noun}。"), RuleEngine()._known_foods)


@pytest.mark.parametrize("noun", NOUNS)
def test_without_dietary_limits_retain_original_with_composition_warning(noun):
    recipe = card(f"豆腐蒸熟后加入{noun}。")
    decision = RuleEngine().evaluate(recipe, Constraints())
    assert decision.allowed and any("成分" in warning for warning in decision.warnings)
    assert not any("高钠" in warning or "辣酱" in warning for warning in decision.warnings)


@pytest.mark.parametrize("extra,restriction", [
    ("辣椒", {"no_spicy": True}), ("花生油", {"allergies": ["花生"]}),
    ("蒜", {"excluded_ingredients": ["蒜"]}), ("蚝油", {"diet_mode": "vegan"}),
])
def test_declared_recipe_does_not_waive_literal_hard_food_requirements(extra, restriction):
    recipe = card(f"烧烤汁由生抽、白糖、水、{extra}组成。豆腐刷上烧烤汁。")
    assert not RuleEngine().evaluate(recipe, Constraints(**restriction)).allowed


def test_current_actual_source_barbecue_fish_is_not_rewritten_or_assumed_safe():
    recipe = next(r for r in catalog().values() if r.name == "烤银鳕鱼")
    assert "烧烤粉5克" in recipe.raw_ingredients and "烧烤汁10克" in recipe.raw_ingredients
    before = recipe.model_dump()
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True, allergies=["花生"])).allowed
    assert recipe.model_dump() == before


def test_actual_new_fish_has_source_sauce_steps_but_cannot_waive_an_egg_allergy():
    recipe = next(r for r in catalog().values() if r.name == "翡翠鳕鱼")
    assert "蛋清15g" in recipe.raw_ingredients and "鸡精" in recipe.steps
    assert "菠菜汁烧开" in recipe.steps and "勾芡汁" in recipe.steps
    rules = RuleEngine()
    assert not unresolved_sauce_evidence(recipe, rules._known_foods)
    assert not rules.evaluate(recipe, Constraints(allergies=["鸡蛋"])).allowed
