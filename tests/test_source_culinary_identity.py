"""Authored declaration/body contrasts, not independent culinary quality labels."""

import pytest

from app.domain.culinary_focus import culinary_food_focus
from app.domain.food_variety import food_families
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine
from evaluation.declared_food_features import observation_food_families
from evaluation.source_culinary_identity import identity_culinary_focus, identity_food_families
from evaluation.whole_menu_guard import prepare_menu_problem, validate_menu_proposal


def dish(name: str, foods: list[str], role: str = "protein") -> Recipe:
    return Recipe(
        recipe_id=name,
        name=name,
        raw_ingredients="、".join(foods),
        ingredients=[Ingredient(name=food, raw=food) for food in foods],
        steps="将食材蒸熟后装盘。",
        categories=[role],
        meal_types=["晚餐"],
        raw_label="晚餐",
        source_row=1,
        fingerprint=name,
    )


@pytest.mark.parametrize(
    "name,foods,wanted",
    [
        ("清蒸鲈鱼", ["鲈鱼"], {"fish"}),
        ("柠汁鳕鱼", ["冷冻鳕鱼片"], {"fish"}),
        ("清蒸龙利鱼", ["龙利鱼"], {"fish"}),
        ("清蒸虾仁", ["鲜虾仁"], {"shrimp"}),
        ("牛肉丸", ["牛肉", "猪肉", "鸡蛋"], {"beef"}),
        ("牛腩", ["牛腩肉"], {"beef"}),
        ("蒸鸭腿", ["鸭腿"], {"duck"}),
        ("蒸肉丸", ["猪肉末", "鸡蛋"], {"pork"}),
        ("蒸肉饼", ["猪肉馅", "豆腐"], {"pork"}),
        ("猪肉包菜卷", ["猪肉", "白菜"], {"pork"}),
        ("白切鸡", ["三黄鸡"], {"chicken"}),
        ("鱼香鸡肉", ["鸡胸肉", "鱼肉"], {"chicken"}),
        ("鸡肉配蒸鸭腿", ["鸡胸肉", "鸭腿"], {"chicken"}),
        ("肉末豆腐", ["猪肉末", "豆腐"], {"tofu"}),
        ("肉末豆腐", ["猪肉馅", "豆腐"], {"tofu"}),
        ("鸡丝豆腐", ["鸡肉", "豆腐"], {"tofu"}),
        ("鸡蛋豆腐", ["全蛋", "豆腐"], {"egg", "tofu"}),
        ("豆腐皮素菜卷", ["豆腐皮", "白菜"], {"tofu"}),
        ("蛋黄蒸肉", ["猪肉末", "鸡蛋"], {"pork"}),
    ],
)
def test_explicit_declared_named_body_not_incidental_animal(
    name: str, foods: list[str], wanted: set[str]
) -> None:
    recipe = dish(name, foods)
    before = recipe.model_dump()
    assert identity_culinary_focus(recipe, "shared_source_v1").families == wanted
    assert recipe.model_dump() == before


@pytest.mark.parametrize(
    "name,foods",
    [
        ("清蒸鲈鱼", ["白菜"]),
        ("虾仁", ["虾酱"]),
        ("牛肉丸", ["肉末"]),
        ("蛋黄蒸肉", ["肉末", "鸡蛋"]),
        ("咸肉蒸蛋", ["咸肉", "鸡蛋"]),
        ("肉丸", ["肉末", "鸡蛋"]),
        ("肉丸", ["猪肉馅", "肉末"]),
        ("肉丸", ["牛肉", "猪肉"]),
        ("鸡腿菇", ["鸡腿菇", "鸡肉"]),
        ("鸭梨", ["鸭梨", "鸭肉"]),
        ("牛肝菌", ["牛肝菌", "牛肉"]),
        ("蛋黄酱豆", ["鸡蛋", "黄豆"]),
        ("鱼汤白菜", ["鱼肉", "白菜"]),
        ("鸡汤白菜", ["鸡肉", "白菜"]),
        ("虾皮白菜", ["虾", "白菜"]),
    ],
)
def test_unknown_compound_homonym_and_stock_do_not_become_main_identity(
    name: str, foods: list[str]
) -> None:
    assert not identity_culinary_focus(dish(name, foods), "shared_source_v1").families


@pytest.mark.parametrize("food", ["虾皮", "虾酱", "鱼露", "牛肉酱", "鸡汤", "肉末", "鸭梨"])
def test_whole_declaration_never_from_substring_or_title(food: str) -> None:
    assert not identity_food_families(dish("鱼虾牛鸭", [food]), "shared_source_v1")


def test_legacy_defaults_and_shared_history_projection_are_unchanged() -> None:
    recipe = dish("牛肉丸", ["牛肉", "猪肉", "鸡蛋"])
    observation = observation_food_families(recipe, feature_policy="shared_v2")
    assert identity_culinary_focus(recipe) == culinary_food_focus(recipe)
    assert identity_food_families(recipe) == food_families(recipe)
    assert identity_food_families(recipe, "shared_source_v1") == {"beef", "pork", "egg"}
    assert observation_food_families(recipe, feature_policy="shared_v2") == observation


def test_source_mutation_role_and_eligibility_cannot_reuse_old_positive_focus() -> None:
    recipe = dish("清蒸鲈鱼", ["鲈鱼"])
    assert identity_culinary_focus(recipe, "shared_source_v1").families == {"fish"}
    recipe.ingredients = [Ingredient(name="鱼露", raw="鱼露")]
    assert not identity_culinary_focus(recipe, "shared_source_v1").families
    recipe.ingredients = [Ingredient(name="鲈鱼", raw="鲈鱼")]
    recipe.categories = ["protein", "soup"]
    assert not identity_culinary_focus(recipe, "shared_source_v1").families
    recipe.categories = ["protein"]
    recipe.steps = "仅将原料混合备用。"
    assert not identity_culinary_focus(recipe, "shared_source_v1").families


def test_new_declarations_do_not_promote_fish_soup_into_a_protein_peer() -> None:
    seed = dish("鸡肉", ["鸡胸肉"])
    fish = dish("鲈鱼", ["鲈鱼"])
    soup = dish("鲈鱼汤", ["鲈鱼"], "soup")
    constraints = Constraints(dish_count=1)
    problem, issue = prepare_menu_problem(
        [seed],
        [seed, fish, soup],
        constraints,
        RuleEngine(),
        food_identity_policy="shared_source_v1",
    )
    assert issue is None and problem is not None
    assert fish.recipe_id in problem.peers[0] and soup.recipe_id not in problem.peers[0]
    assert validate_menu_proposal(problem, [fish]) == []
    forged = fish.model_copy(deep=True)
    forged.steps = "新编造的做法。"
    assert validate_menu_proposal(problem, [forged]) == ["unbound_or_modified_source"]


@pytest.mark.parametrize(
    "constraints",
    [
        Constraints(dish_count=1, allergies=["鱼"]),
        Constraints(dish_count=1, excluded_ingredients=["鲈鱼"]),
        Constraints(dish_count=1, no_spicy=True),
        Constraints(dish_count=1, diet_mode="vegan"),
    ],
)
def test_new_identity_does_not_bypass_hard_dietary_gates(constraints: Constraints) -> None:
    seed = dish("蒸豆腐", ["豆腐"])
    fish = dish("鲈鱼", ["鲈鱼", "辣椒"])
    problem, issue = prepare_menu_problem(
        [seed], [seed, fish], constraints, RuleEngine(), food_identity_policy="shared_source_v1"
    )
    assert issue is None and problem is not None
    assert fish.recipe_id not in problem.peers[0]


def test_local_boundary_keeps_other_slot_frozen_with_new_identity() -> None:
    seed = [dish("鸡肉", ["鸡肉"]), dish("蒸豆腐", ["豆腐"])]
    fish = dish("鲈鱼", ["鲈鱼"])
    problem, issue = prepare_menu_problem(
        seed,
        [*seed, fish],
        Constraints(dish_count=2),
        RuleEngine(),
        replace_slot=2,
        food_identity_policy="shared_source_v1",
    )
    assert issue is None and problem is not None
    assert problem.peers[0] == [seed[0].recipe_id]
    assert "slot_role_meal_query_identity_or_edit_boundary" in validate_menu_proposal(
        problem, [fish, seed[1]]
    )
