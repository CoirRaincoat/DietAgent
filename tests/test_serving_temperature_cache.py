"""Synthetic regressions for reuse of source-derived serving temperatures."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agent.menu_balance import _serving_temperature, analyze_menu_balance, serving_temperature
from app.agent.planner import MenuPlanner
from app.agent.service import MealAgent
from app.domain.models import Constraints, Ingredient, Intent, Recipe
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.synthetic import synthetic_profiles
from app.rules.engine import RuleEngine


def source_recipe(**updates):
    fields = {
        "recipe_id": "synthetic-same-id", "fingerprint": "synthetic-version", "source_row": 2,
        "name": "合成蔬菜", "raw_ingredients": "青菜、水",
        "ingredients": [Ingredient(raw="青菜", name="青菜"), Ingredient(raw="水", name="水")],
        "steps": "将青菜蒸熟后装盘。", "categories": ["vegetable"], "methods": ["蒸"],
    }
    fields.update(updates)
    return Recipe(**fields)


@pytest.fixture(autouse=True)
def isolated_temperature_cache():
    _serving_temperature.cache_clear()
    yield
    _serving_temperature.cache_clear()


@pytest.mark.parametrize(
    ("initial", "changes", "before", "after"),
    [
        ({}, {"steps": "青菜焯水后放凉装盘。"}, "hot", "cold"),
        ({}, {"name": "凉拌合成蔬菜"}, "hot", "cold"),
        ({"name": "凉拌合成蔬菜"}, {"name": "合成蔬菜"}, "cold", "hot"),
        ({"steps": "开始烹饪后装盘。"}, {"methods": []}, "hot", "unknown"),
        ({"steps": "开始烹饪后装盘。", "methods": []}, {"methods": ["蒸"]}, "unknown", "hot"),
        ({}, {"steps": "蒸熟后趁热食用，也可放凉冷吃。"}, "hot", "unknown"),
        ({"steps": "食材冷藏腌制备用。"}, {"steps": "食材冷藏腌制后蒸熟趁热食用。"}, "unknown", "hot"),
    ],
)
def test_mutating_source_fields_never_reuses_an_old_temperature(initial, changes, before, after):
    recipe = source_recipe(**initial)
    assert serving_temperature(recipe) == before
    assert serving_temperature(recipe) == before
    for name, value in changes.items():
        setattr(recipe, name, value)
    assert serving_temperature(recipe) == after
    assert analyze_menu_balance([recipe]).temperature_counts[after] == 1


def test_in_place_method_change_is_seen_after_a_warm_lookup():
    recipe = source_recipe(steps="开始烹饪后装盘。", methods=[])
    assert serving_temperature(recipe) == "unknown"
    recipe.methods.append("煮")
    assert serving_temperature(recipe) == "hot"
    recipe.methods.clear()
    assert serving_temperature(recipe) == "unknown"


@pytest.mark.parametrize(
    ("new_fields", "expected"),
    [({"steps": "焯水后过凉水，拌匀即可。"}, "cold"),
     ({"name": "凉拌合成蔬菜"}, "cold"),
     ({"steps": "开始烹饪后装盘。", "methods": []}, "unknown")],
)
def test_distinct_source_objects_with_the_same_id_and_fingerprint_stay_independent(new_fields, expected):
    first = source_recipe()
    newer = source_recipe(**new_fields)
    assert first.recipe_id == newer.recipe_id and first.fingerprint == newer.fingerprint
    assert serving_temperature(first) == "hot"
    assert serving_temperature(newer) == expected
    assert serving_temperature(first) == "hot"


def test_shared_source_reuse_does_not_cache_health_decisions_or_menus():
    rules, recipes = RuleEngine(), []
    specifications = [
        ("egg", "鸡蛋", ["protein"], ["蒸"]),
        ("chicken", "鸡胸肉", ["protein"], ["煮"]),
        ("greens", "青菜", ["vegetable"], ["炒"]),
        ("rice", "大米", ["staple"], ["煮"]),
    ]
    for identity, ingredient, categories, methods in specifications:
        recipes.append(source_recipe(
            recipe_id=identity, fingerprint=identity, name="合成" + ingredient,
            raw_ingredients=ingredient, ingredients=[Ingredient(raw=ingredient, name=ingredient)],
            steps="将" + ingredient + "蒸熟后装盘。", categories=categories, methods=methods,
        ))
    unrestricted = Constraints(preferred_ingredients=["鸡蛋"])
    restricted = unrestricted.model_copy(update={"allergies": ["鸡蛋"]}, deep=True)
    planner = MenuPlanner(rules)
    first = planner.plan(recipes, unrestricted, query_terms=["鸡蛋"])
    assert first.failure is None and "egg" in {recipe.recipe_id for recipe in first.recipes}
    for recipe in recipes:
        serving_temperature(recipe)
    second = planner.plan(recipes, restricted, current=first.recipes, query_terms=["鸡蛋"])
    assert second.failure is None and len(second.recipes) == 3
    assert "egg" not in {recipe.recipe_id for recipe in second.recipes}
    assert not rules.evaluate(recipes[0], restricted).allowed
    third = planner.plan(recipes, unrestricted, query_terms=["鸡蛋"])
    assert [recipe.recipe_id for recipe in third.recipes] == [recipe.recipe_id for recipe in first.recipes]


def test_concurrent_source_versions_return_their_own_temperature():
    versions = [source_recipe(), source_recipe(steps="食材焯水后放凉装盘。"),
                source_recipe(steps="将食材拌匀装盘。", methods=[])]
    requests = [(versions[index % 3], ("hot", "cold", "unknown")[index % 3]) for index in range(180)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        actual = list(executor.map(serving_temperature, [recipe for recipe, _ in requests]))
    assert actual == [expected for _, expected in requests]


def test_cache_has_a_fixed_bound_and_eviction_preserves_source_results():
    maximum = _serving_temperature.cache_info().maxsize
    assert maximum is not None and 0 < maximum <= 4096
    original = source_recipe()
    assert serving_temperature(original) == "hot"
    for index in range(maximum + 1):
        assert serving_temperature(source_recipe(name=f"合成蔬菜{index}")) == "hot"
    assert _serving_temperature.cache_info().currsize <= maximum
    assert serving_temperature(original) == "hot"
    original.steps = "食材焯水后放凉装盘。"
    assert serving_temperature(original) == "cold"


def test_cache_does_not_keep_mutable_recipe_instances_alive():
    recipe = source_recipe()
    reference = weakref.ref(recipe)
    assert serving_temperature(recipe) == "hot"
    del recipe
    gc.collect()
    assert reference() is None


@pytest.mark.asyncio
async def test_shared_agent_keeps_two_synthetic_users_allergies_and_menus_separate(tmp_path):
    class FixedSyntheticLLM(BaseLLM):
        async def parse(self, message, state, profile):
            return Intent(people=1, meal_type="晚餐", dish_count=3, restrictions_confirmed=True,
                          allergies=list(profile.allergies),
                          preferred_ingredients=["鸡蛋"], query_terms=["鸡蛋"])

        async def explain(self, facts):
            return list(facts)

        async def aclose(self):
            pass

    profiles = synthetic_profiles()
    profiles[900002].allergies = ["鸡蛋"]
    profiles[900002].health_goals = []
    recipes = {}
    for identity, ingredient, role in (("egg", "鸡蛋", "protein"), ("chicken", "鸡胸肉", "protein"),
                                       ("greens", "青菜", "vegetable"), ("rice", "大米", "staple")):
        recipes[identity] = source_recipe(
            recipe_id=identity, name="合成" + ingredient, fingerprint=identity,
            raw_ingredients=ingredient, ingredients=[Ingredient(raw=ingredient, name=ingredient)],
            steps="将" + ingredient + "蒸熟后装盘。", categories=[role],
        )
    catalog = DataCatalog(profiles=profiles, recipes=recipes, quality_report={"data_scope": "synthetic"})
    store = SessionStore(tmp_path / "synthetic-cache-isolation.sqlite3")
    agent = MealAgent(catalog, store, FixedSyntheticLLM())
    first = await agent.chat(900001, "1人晚餐3道，想吃鸡蛋，没有其他忌口", request_id="synthetic-user-a")
    second = await agent.chat(900002, "1人晚餐3道，鸡蛋过敏", request_id="synthetic-user-b")
    assert first.status == second.status == "ok", second.reason
    assert "egg" in {item.recipe_id for item in first.menu}
    assert "egg" not in {item.recipe_id for item in second.menu}
    assert first.conversation_state.session_id != second.conversation_state.session_id
    assert second.conversation_state.constraints.allergies == ["鸡蛋"]
    assert store.get(first.conversation_state.session_id, 900001).constraints.allergies == []
