"""Dish-category exclusions must not become unsupported ingredient bans."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine


def dish(*foods, name="蒸鸡蛋", categories=None, labels=None, steps="加水蒸熟后装盘。"):
    return Recipe(
        recipe_id="public-role-exclusion", source_row=2, fingerprint="public-role-exclusion",
        name=name, raw_ingredients="；".join(foods),
        ingredients=[Ingredient(raw=food, name=food) for food in foods], steps=steps,
        categories=categories if categories is not None else ["protein"], labels=labels or [],
    )


@pytest.fixture(scope="module")
def rules():
    return RuleEngine()


@pytest.mark.parametrize("exclusions", [["甜品"], ["饮料"], ["甜品", "饮料"]])
def test_category_exclusion_does_not_block_grounded_meal_dish(rules, exclusions):
    constraints = Constraints(excluded_ingredients=exclusions, no_spicy=True)
    before = constraints.model_dump()
    assert rules.unresolved_exclusions(constraints) == []
    assert rules.evaluate(dish("鸡蛋", "水"), constraints).allowed
    assert constraints.model_dump() == before  # Keep the original user requirement.


@pytest.mark.parametrize("recipe", [
    dish("苹果", "水", name="苹果汁", categories=["drink"], steps="加水打成果汁饮用。"),
    dish("面粉", "鸡蛋", name="蛋糕", categories=["dessert"], steps="混合后烤熟。"),
    dish("面粉", "鸡蛋", name="蛋糕", categories=["protein"], steps="混合后烤熟。"),
    dish("鸡蛋", "水", categories=["protein"], labels=["甜品"]),
    dish("鸡蛋", "水", categories=[]),
    dish("鸡蛋", "水", categories=["component"]),
])
def test_category_exclusion_uses_existing_source_role_gate_not_just_cached_categories(rules, recipe):
    decision = rules.evaluate(recipe, Constraints(excluded_ingredients=["甜品", "饮料"]))
    assert not decision.allowed
    assert any("正餐角色" in reason for reason in decision.reasons)


@pytest.mark.parametrize("unknown", ["神秘食材", "饮料粉", "甜品配料", "不要甜品", "甜味"])
def test_similar_or_unknown_ingredient_exclusion_still_fails_closed(rules, unknown):
    constraints = Constraints(excluded_ingredients=["甜品", "饮料", unknown])
    assert rules.unresolved_exclusions(constraints) == [unknown]
    assert not rules.evaluate(dish("鸡蛋", "水"), constraints).allowed


@pytest.mark.parametrize("allergy", ["甜品", "饮料", "神秘食材"])
def test_category_words_in_allergies_are_not_reinterpreted_or_dropped(rules, allergy):
    constraints = Constraints(excluded_ingredients=["甜品", "饮料"], allergies=[allergy])
    assert rules.unresolved_allergies(constraints) == [allergy]
    assert not rules.evaluate(dish("鸡蛋", "水"), constraints).allowed


@pytest.mark.parametrize("extra,recipe", [
    ({"allergies": ["花生"]}, dish("鸡蛋", "水", steps="蒸熟后可选撒花生碎。")),
    ({"excluded_ingredients": ["甜品", "饮料", "鸡蛋"]}, dish("鸡蛋", "水")),
    ({"no_spicy": True}, dish("鸡蛋", "辣椒", "水")),
    ({"diet_mode": "vegan"}, dish("鸡蛋", "水")),
    ({"max_minutes": 30}, dish("鸡蛋", "水")),
])
def test_category_exclusion_never_waives_food_or_diet_safety(rules, extra, recipe):
    constraints = Constraints.model_validate({"excluded_ingredients": ["甜品", "饮料"], **extra})
    assert not rules.evaluate(recipe, constraints).allowed
