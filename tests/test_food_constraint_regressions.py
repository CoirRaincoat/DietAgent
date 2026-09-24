"""Source-backed regressions for culinary membership, not clinical inference."""

import csv
from pathlib import Path

import pytest
import yaml

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient, Recipe
from app.infrastructure.data import normalize_recipes
from app.rules.engine import DEFAULT_RULES_PATH, RuleEngine


def recipe(*ingredients, name="蒸蔬菜", steps="放入锅中煮熟。"):
    return Recipe(
        recipe_id="food-regression", name=name, source_row=2, fingerprint="regression",
        raw_ingredients="；".join(ingredients),
        ingredients=[Ingredient(raw=item, name=item) for item in ingredients], steps=steps,
    )


@pytest.fixture(scope="module")
def rules():
    return RuleEngine()


@pytest.fixture(scope="module")
def source_recipes():
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        return normalize_recipes(csv.DictReader(stream))


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
@pytest.mark.parametrize("food,ingredient", [
    ("猪肉", "猪小排"), ("猪肉", "猪五花肉"), ("猪肉", "猪后腿肉"),
    ("猪肉", "排骨"), ("猪肉", "猪油"), ("猪肉", "猪蹄"),
    ("鸡肉", "鸡翅中"), ("鸡肉", "鸡腿"), ("鸡肉", "鸡爪"),
    ("鸡肉", "整鸡"), ("鸡肉", "鸡"), ("鸡肉", "鸡胸肉"),
    ("牛肉", "牛腩"), ("牛肉", "牛小排"), ("牛肉", "肥牛卷"),
    ("牛肉", "牛排"), ("牛肉", "牛腱肉"), ("牛肉", "牛仔骨"),
    ("小麦", "吐司"), ("小麦", "全麦吐司片"), ("小麦", "法棍"),
    ("小麦", "土司"), ("小麦", "全麦粉"), ("小麦", "意粉"),
])
def test_explicit_culinary_members_fail_both_hard_constraints(rules, field, food, ingredient):
    decision = rules.evaluate(recipe(ingredient), Constraints(**{field: [food]}))
    assert not decision.allowed
    assert any("命中配料或步骤" in reason for reason in decision.reasons)


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
@pytest.mark.parametrize("food,ingredients", [
    ("鸡肉", ["鸡腿菇", "鸡头米", "黑鸡枞", "鸡蛋", "素鸡"]),
    ("鸡肉", ["火鸡腿", "牛肉", "植物鸡肉"]),
    ("牛肉", ["牛肝菌", "牛油果", "牛奶", "牛蛙"]),
    ("猪肉", ["牛排骨", "羊肋排", "牛肉末", "鸡肉丸"]),
    ("小麦", ["大米", "玉米淀粉", "鸡蛋", "白菜"]),
])
def test_lookalikes_and_other_foods_are_not_cross_classified(rules, field, food, ingredients):
    assert rules.evaluate(recipe(*ingredients), Constraints(**{field: [food]})).allowed


@pytest.mark.parametrize("food,ingredient", [
    ("猪肉", "猪小排"), ("鸡肉", "鸡翅"), ("牛肉", "牛腩"), ("小麦", "吐司"),
])
def test_steps_are_screened_but_dish_titles_are_not_food_evidence(rules, food, ingredient):
    constraints = Constraints(excluded_ingredients=[food])
    assert rules.evaluate(recipe("白菜", name=ingredient + "风味白菜"), constraints).allowed
    step_only = recipe("白菜", steps=f"白菜煮熟后可选加入{ingredient}。")
    assert not rules.evaluate(step_only, constraints).allowed


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
@pytest.mark.parametrize("food,ingredient", [
    ("猪肉", "火腿肠"), ("鸡肉", "鸡精"), ("牛肉", "肉丸"),
])
def test_uncertain_meat_source_is_not_asserted_as_membership(rules, field, food, ingredient):
    sample = recipe(ingredient)
    decision = rules.evaluate(sample, Constraints(**{field: [food]}))
    assert not decision.allowed
    assert any("来源不明" in reason for reason in decision.reasons)
    assert not any("命中配料或步骤" in reason for reason in decision.reasons)
    # Unknown composition must not satisfy a preference or ban unrelated foods.
    assert rules.food_matches(sample, food) == []
    assert rules.evaluate(sample, Constraints(preferred_ingredients=[food])).score == 0
    assert rules.evaluate(sample, Constraints(allergies=["苹果"])).allowed
    assert rules.evaluate(sample, Constraints()).allowed


def test_masked_names_do_not_hide_a_second_real_ingredient(rules):
    assert not rules.evaluate(recipe("牛肝菌", "牛肝"), Constraints(allergies=["牛肉"])).allowed
    assert not rules.evaluate(recipe("鸡腿菇", "鸡腿"), Constraints(allergies=["鸡肉"])).allowed


def test_directional_membership_does_not_expand_inventory_substitution(rules):
    sample = recipe("鸡翅", steps="食材蒸熟。")
    assert rules.food_matches(sample, "鸡肉") == ["鸡翅"]
    assert rules.inventory_missing(sample, ["鸡胸肉"]) == ["鸡翅"]
    assert rules.canonical_food("鸡翅") == "鸡翅"


def test_legacy_rule_config_without_food_families_remains_loadable(tmp_path):
    config = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    config.pop("food_families")
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    legacy = RuleEngine(path)
    assert not legacy.evaluate(recipe("鸡胸肉"), Constraints(allergies=["鸡肉"])).allowed
    assert legacy.evaluate(recipe("白菜"), Constraints(allergies=["鸡肉"])).allowed


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
@pytest.mark.parametrize("food,recipe_id,raw_evidence", [
    ("猪肉", "recipe_d365f868acdf878e723fd849", "猪小排500克"),
    ("鸡肉", "recipe_bc78ae0fe4a5963d51c5afbd", "鸡翅3个"),
    ("小麦", "recipe_f0cd40f0e8beda4d65de76d9", "吐司2片"),
    ("牛肉", "recipe_7ad0ac307230e12adfcc46ec", "牛排1块"),
])
def test_real_catalog_counterexamples_are_removed_from_current_menu(
    rules, source_recipes, field, food, recipe_id, raw_evidence,
):
    original = source_recipes[recipe_id]
    assert raw_evidence in original.raw_ingredients
    constraints = Constraints(dish_count=1, **{field: [food]})
    assert not rules.evaluate(original, constraints).allowed
    # Revalidate an existing offending dish against the actual complete catalog.
    planned = MenuPlanner(rules).plan(list(source_recipes.values()), constraints, current=[original])
    assert planned.failure is None
    assert original.recipe_id not in {item.recipe_id for item in planned.recipes}
