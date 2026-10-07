"""Source counterexamples: mustard and unspecified green/red pepper varieties."""

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from app.infrastructure.synthetic import load_synthetic_catalog
from app.rules.engine import RuleEngine
from evaluation.no_spicy_oracle import no_spicy_findings


def recipe(food, *, steps="蒸熟后装盘。", parsed_name=None):
    return Recipe(recipe_id="public-pepper", name="蒸菜", source_row=2, fingerprint="pepper",
        raw_ingredients=food, ingredients=[Ingredient(raw=food, name=parsed_name or food)],
        steps=steps, categories=["vegetable"], labels=["不辣"])


@pytest.fixture(scope="module")
def rules():
    return RuleEngine()


@pytest.mark.parametrize("food", ["芥末2克", "芥末酱", "黄芥末酱", "芥末油"])
@pytest.mark.parametrize("origin", ["ingredients", "steps"])
def test_mustard_rejected_from_source_even_when_label_says_non_spicy(rules, food, origin):
    sample = recipe(food) if origin == "ingredients" else recipe("白菜", steps=f"蒸熟后可选加入{food}。")
    before = sample.model_dump()
    decision = rules.evaluate(sample, Constraints(no_spicy=True))
    assert not decision.allowed and "芥末" in "；".join(decision.reasons)
    assert rules.evaluate(sample, Constraints()).allowed
    assert sample.model_dump() == before


@pytest.mark.parametrize("food", ["青椒", "红椒", "青红椒", "青椒粒", "红椒粒"])
@pytest.mark.parametrize("origin", ["ingredients", "steps"])
def test_unqualified_pepper_cannot_be_promised_non_spicy(rules, food, origin):
    sample = recipe(food) if origin == "ingredients" else recipe("白菜", steps=f"加入{food}炒熟。")
    decision = rules.evaluate(sample, Constraints(no_spicy=True))
    assert not decision.allowed
    assert "品种未明确" in "；".join(decision.reasons)
    assert rules.evaluate(sample, Constraints()).allowed


@pytest.mark.parametrize("food", ["甜椒", "彩椒", "柿子椒", "青甜椒", "红甜椒", "芥蓝", "芥菜"])
def test_other_peppers_and_similar_names_are_not_blanket_banned(rules, food):
    assert rules.evaluate(recipe(food), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("food,name", [
    ("青椒（甜椒）20克", "青椒"),
    ("红椒（彩椒）20克", "红椒"),
    ("青红椒（甜椒）20克", "青红椒"),
    ("青椒(柿子椒)20克", "青椒"),
])
def test_direct_source_variety_binding_survives_parsed_name_and_step_shorthand(rules, food, name):
    sample = recipe(food, parsed_name=name, steps=f"将{name}切块炒熟。")
    assert rules.evaluate(sample, Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("food", ["青椒、彩椒", "青椒（可用甜椒）", "红椒（甜椒或尖椒）", "青椒（甜椒）、红椒"])
def test_another_sweet_pepper_or_optional_substitution_does_not_clear_unknown_variety(rules, food):
    assert not rules.evaluate(recipe(food), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("food", ["芥末", "青椒", "红椒", "青红椒"])
def test_existing_acceptance_oracle_sees_newly_exposed_source_evidence(food):
    assert no_spicy_findings(food, ["不辣"])


@pytest.mark.parametrize("food", ["芥末", "青椒", "红椒"])
def test_light_preference_explicitly_no_spicy_uses_same_guard(rules, food):
    assert not rules.evaluate(recipe(food), Constraints(preferences=["不辣"])).allowed


@pytest.mark.parametrize("name", ["柠汁煎鳕鱼", "蒜蓉粉丝龙利鱼"])
def test_actual_previous_menu_source_is_rejected_not_rewritten(name):
    catalog = load_synthetic_catalog()
    sample = next(r for r in catalog.recipes.values() if r.name == name)
    before = sample.model_dump()
    assert not RuleEngine().evaluate(sample, Constraints(no_spicy=True)).allowed
    assert sample.model_dump() == before
