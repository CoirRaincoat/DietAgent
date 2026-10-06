"""R0: public CSV counterexample plus explicitly synthetic recipes and intents.

The historical J20/t1 suggestion was identified from the saved strict response;
tests pin its public CSV identity, not model ranking or a private user profile.
"""

import csv
from pathlib import Path

import pytest

from app.agent.service import MealAgent
from app.domain.models import Constraints, Ingredient, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.rules.engine import RuleEngine

OLD_J20_ID = "recipe_09e068be33ab179e5b862369"
OLD_J20_FINGERPRINT = "09e068be33ab179e5b862369ccfbc5471d74291763216aac0b31e66250f004fa"


@pytest.fixture(scope="module")
def old_j20_recipe():
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        item = normalize_recipes(csv.DictReader(stream))[OLD_J20_ID]
    assert item.name == "猪肉包菜卷"
    assert item.source_row == 1681 and item.fingerprint == OLD_J20_FINGERPRINT
    assert "西红柿45克" in item.raw_ingredients and "番茄酱15克" in item.raw_ingredients
    assert "西红柿" in item.steps and "番茄酱" in item.steps
    return item


def synthetic_recipe(key="safe", ingredient="白菜", steps="将食材蒸熟。"):
    return Recipe(
        recipe_id=f"synthetic-{key}", name=f"合成蒸菜{key}",
        raw_ingredients=ingredient,
        ingredients=[Ingredient(raw=ingredient, name=ingredient)], steps=steps,
        categories=["vegetable"], methods=["蒸"], meal_types=["晚餐"],
        source_row=1, fingerprint=f"synthetic-{key}",
    )


@pytest.mark.parametrize("term", ["洋柿子", "西红柿", "番茄"])
def test_fixed_old_j20_record_is_excluded_without_reordering(old_j20_recipe, term):
    rules = RuleEngine()
    before = old_j20_recipe.model_dump()
    decision = rules.evaluate(old_j20_recipe, Constraints(excluded_ingredients=[term]))
    assert not decision.allowed
    assert any("命中配料或步骤" in reason for reason in decision.reasons)
    assert old_j20_recipe.model_dump() == before


@pytest.mark.parametrize("term,ingredient", [
    ("洋柿子", "番茄"), ("番茄", "洋柿子"), ("西红柿", "洋柿子"),
    ("芫荽", "香菜"), ("香菜", "芫荽"),
    ("马铃薯", "土豆"), ("土豆", "马铃薯"),
])
@pytest.mark.parametrize("location", ["ingredient", "step"])
def test_aliases_screen_ingredient_and_optional_step_text(term, ingredient, location):
    recipe = synthetic_recipe(
        ingredient=ingredient if location == "ingredient" else "白菜",
        steps=f"蒸熟后可选撒上{ingredient}。" if location == "step" else "蒸熟。",
    )
    decision = RuleEngine().evaluate(recipe, Constraints(excluded_ingredients=[term]))
    assert not decision.allowed
    assert any("命中配料或步骤" in reason for reason in decision.reasons)


@pytest.mark.parametrize("term", ["洋柿子", "西红柿", "番茄", "芫荽", "香菜", "马铃薯", "土豆"])
def test_safe_synthetic_food_is_not_rejected_for_recognized_alias(term):
    assert RuleEngine().evaluate(
        synthetic_recipe(), Constraints(excluded_ingredients=[term]),
    ).allowed


@pytest.mark.parametrize("term", ["芫荽", "香菜"])
def test_coriander_allergy_alias_has_same_known_mapping(term):
    rules = RuleEngine()
    constraints = Constraints(allergies=[term])
    assert rules.unresolved_allergies(constraints) == []
    assert not rules.evaluate(synthetic_recipe(ingredient="香菜"), constraints).allowed
    assert rules.evaluate(synthetic_recipe(), constraints).allowed


def test_tomato_sauce_membership_does_not_make_it_an_inventory_alias():
    rules = RuleEngine()
    recipe = synthetic_recipe(ingredient="番茄酱")
    assert not rules.evaluate(recipe, Constraints(excluded_ingredients=["洋柿子"])).allowed
    assert rules.canonical_food("番茄酱") == "番茄酱"
    assert rules.canonical_food("洋柿子") == "番茄"
    assert rules.inventory_missing(recipe, ["洋柿子"]) == ["番茄酱"]


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
def test_unknown_exact_food_does_not_gain_verified_safety_from_no_match(field):
    decision = RuleEngine().evaluate(
        synthetic_recipe(), Constraints(**{field: ["合成未知食材甲"]}),
    )
    assert not decision.allowed
    assert any("缺少已支持映射" in reason for reason in decision.reasons)


@pytest.mark.parametrize("field", ["allergies", "excluded_ingredients"])
def test_unknown_composite_is_not_assumed_tomato_free(field):
    decision = RuleEngine().evaluate(
        synthetic_recipe(ingredient="沙拉酱"), Constraints(**{field: ["番茄"]}),
    )
    assert not decision.allowed
    assert any("复合配料" in reason for reason in decision.reasons)


class SyntheticFactsLLM(BaseLLM):
    async def parse(self, message, state, profile):
        raise AssertionError("R0 direct planning tests do not invoke parse")

    async def explain(self, facts):
        return list(facts)

    async def aclose(self):
        pass


@pytest.mark.parametrize("channel", ["menu", "replacement_suggestions"])
async def test_actual_planner_and_suggestion_channels_screen_fixed_old_record(
    old_j20_recipe, tmp_path, monkeypatch, channel,
):
    # Safe candidates contain only cabbage, rice and tofu; enough remain for
    # a complete three-dish menu and two suggestions after rejecting the old row.
    safe = [synthetic_recipe(str(index)) for index in range(6)]
    for item in safe:
        item.name = "合成白菜豆腐饭" + item.recipe_id
        item.raw_ingredients = "白菜；大米；豆腐"
        item.ingredients = [Ingredient(raw=value, name=value) for value in ["白菜", "大米", "豆腐"]]
        # Keep the original source row and its current primary-role metadata;
        # alias safety is not permission to restore old mixed-ingredient roles.
        item.categories = list(old_j20_recipe.categories)
    assert set(old_j20_recipe.categories) == set(safe[0].categories)
    profile = UserProfile(
        data_scope="synthetic", user_id=900001, age=30, sex="女",
        height_cm=165, weight_kg=55, bmi=20.2,
    )
    catalog = DataCatalog(
        profiles={profile.user_id: profile},
        recipes={item.recipe_id: item for item in [old_j20_recipe, *safe]},
        quality_report={},
    )
    agent = MealAgent(catalog, SessionStore(tmp_path / "r0.sqlite"), SyntheticFactsLLM())
    candidates = [old_j20_recipe, *safe] if channel == "menu" else [
        *safe[:3], old_j20_recipe, *safe[3:],
    ]
    # The tested old row is explicitly present. Retrieval ranking cannot hide it.
    monkeypatch.setattr(agent.retriever, "search", lambda *args, **kwargs: list(candidates))
    constraints = Constraints(
        people=1, dish_count=3, soup_count=0, meal_type="晚餐",
        excluded_ingredients=["马铃薯", "洋柿子", "芫荽"],
    )
    current = [old_j20_recipe, *safe[:2]] if channel == "menu" else safe[:3]
    state = SessionState(
        session_id="synthetic-r0-session", user_id=profile.user_id,
        constraints=constraints, meal_constraints=constraints.model_copy(deep=True),
        confirmed_fields=["people", "meal_type", "restrictions"],
        menu_ids=[recipe.recipe_id for recipe in current],
    )
    result = await agent._plan(state, Intent(), list(state.menu_ids), [])
    assert result.status == "ok" and len(result.menu) == 3
    assert OLD_J20_ID not in {item.recipe_id for item in result.menu}
    assert OLD_J20_ID not in {item.recipe_id for item in result.replacement_suggestions}
    assert len(result.replacement_suggestions) == 2
    assert all(item.recipe_id.startswith("synthetic-") for item in [
        *result.menu, *result.replacement_suggestions,
    ])
    assert result.conversation_state.constraints.excluded_ingredients == [
        "马铃薯", "洋柿子", "芫荽",
    ]
