"""no_spicy lexical-coverage gap regression (油泼辣子 / 辣子 / 小米辣).

The J17 real-model eval surfaced a rule-coverage gap: no_spicy=true was correctly
set in state, but 川北凉粉 (油泼辣子) stayed in the menu because the spicy_terms
lexicon had no term matching 辣子 / 油泼辣子 / 小米辣. This suite pins the fix:
clear spicy terms are rejected deterministically, while 甜椒 / 彩椒 / 柿子椒 and
no_spicy=false are not over-blocked.
"""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient, Recipe
from app.infrastructure.synthetic import load_synthetic_catalog
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine


@pytest.fixture(scope="module")
def engine():
    return RuleEngine()


@pytest.fixture(scope="module")
def catalog():
    # These assertions use public recipe rows, never private health profiles.
    return load_synthetic_catalog()


def _recipe_by_name(catalog, name):
    for recipe in catalog.recipes.values():
        if recipe.name == name:
            return recipe
    raise AssertionError(f"recipe not found: {name}")


def _make_recipe(ingredients, *, name="测试菜", steps="放入锅中煮熟。"):
    return Recipe(
        recipe_id="FT-R-spicy", name=name, source_row=1, fingerprint="spicy",
        raw_ingredients="、".join(ingredients),
        ingredients=[Ingredient(raw=value, name=value) for value in ingredients],
        steps=steps, labels=[],
    )


# S1 真实反例：川北凉粉（含油泼辣子）在 no_spicy=true 下必须被拒。
def test_s1_chuanbei_liangfen_rejected_under_no_spicy(engine, catalog):
    recipe = _recipe_by_name(catalog, "川北凉粉")
    decision = engine.evaluate(recipe, Constraints(no_spicy=True))
    assert not decision.allowed
    assert any("不辣要求命中辣味配料" in reason for reason in decision.reasons)


# 其余含“辣子 / 油泼辣子”的真实菜谱同样被拒（非菜名特判，词表修复的泛化证据）。
@pytest.mark.parametrize("recipe_name", ["南洋牛肉干", "紫苏辣子鸡"])
def test_real_data_la_zi_gap_recipes_rejected(engine, catalog, recipe_name):
    recipe = _recipe_by_name(catalog, recipe_name)
    decision = engine.evaluate(recipe, Constraints(no_spicy=True))
    assert not decision.allowed
    assert any("不辣要求命中辣味配料" in reason for reason in decision.reasons)


# S2 辣子
def test_s2_la_zi_rejected(engine):
    recipe = _make_recipe(["鸡腿肉", "辣子"], name="辣子鸡")
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S3 干辣椒
def test_s3_gan_la_jiao_rejected(engine):
    recipe = _make_recipe(["白菜", "干辣椒"])
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S4 辣酱 / 辣椒酱
@pytest.mark.parametrize("spicy_term", ["辣酱", "辣椒酱"])
def test_s4_la_jiang_rejected(engine, spicy_term):
    recipe = _make_recipe(["豆腐", spicy_term])
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S5 红油
def test_s5_hong_you_rejected(engine):
    recipe = _make_recipe(["面条", "红油"])
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S6 剁椒 / 泡椒
@pytest.mark.parametrize("spicy_term", ["剁椒", "泡椒"])
def test_s6_duo_pao_jiao_rejected(engine, spicy_term):
    recipe = _make_recipe(["鱼", spicy_term])
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S7 false-positive：甜椒 / 彩椒 / 柿子椒 不得仅因“椒”被拒。
@pytest.mark.parametrize("sweet_pepper", ["甜椒", "彩椒", "柿子椒"])
def test_s7_sweet_peppers_not_rejected(engine, sweet_pepper):
    recipe = _make_recipe([sweet_pepper, "猪肉"], steps=f"将猪肉与{sweet_pepper}切块炒熟。")
    assert engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


# S8 no_spicy=false：即便含辣味词，也不得被这条规则无条件拦截。
def test_s8_no_spicy_false_not_blocked(engine):
    recipe = _make_recipe(["鸡腿肉", "油泼辣子"], name="辣子鸡")
    assert engine.evaluate(recipe, Constraints(no_spicy=False)).allowed


# planner 层回归：no_spicy=true 的最终菜单不含辣子类菜，且不整体不可行。
def test_planner_no_spicy_excludes_la_zi_and_stays_feasible(catalog):
    constraints = Constraints(dish_count=3, no_spicy=True)
    recipes = list(catalog.recipes.values())
    candidates = KeywordRetriever(recipes).search([], constraints)
    result = MenuPlanner(RuleEngine()).plan(candidates, constraints)
    assert result.failure is None
    names = {recipe.name for recipe in result.recipes}
    assert not ({"川北凉粉", "南洋牛肉干", "紫苏辣子鸡"} & names)
