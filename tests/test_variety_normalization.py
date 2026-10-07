"""Authored ingredient/form counterexamples, not nutrient or safety aliases."""

from collections.abc import Sequence

import pytest

from app.agent.menu_variety import repair_menu_variety
from app.agent.planner import MenuPlanner
from app.domain.culinary_focus import culinary_food_focus
from app.domain.food_variety import food_families
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine


def dish(name: str, foods: Sequence[str], role: str = "staple") -> Recipe:
    """Construct a public developer fixture; original recipe records stay local."""
    return Recipe(
        recipe_id=name,
        name=name,
        raw_ingredients="、".join(foods),
        ingredients=[Ingredient(raw=food, name=food) for food in foods],
        steps="食材加水蒸熟后取出装盘。",
        categories=[role],
        methods=["蒸"],
        meal_types=["晚餐"],
        source_row=17,
        fingerprint=name,
    )


@pytest.mark.parametrize("food", ["米", " 米 ", "生米", "熟米", "米粒"])
def test_literal_rice_declaration_has_finite_rice_family(food: str) -> None:
    assert food_families(dish("配方", [food])) == frozenset({"rice"})


@pytest.mark.parametrize("food", ["南豆腐", "嫩南豆腐", "南豆腐块", "南豆腐片"])
def test_southern_tofu_declaration_has_finite_tofu_family(food: str) -> None:
    assert food_families(dish("配方", [food], "protein")) == frozenset({"tofu"})


@pytest.mark.parametrize(
    "food",
    [
        "米醋",
        "米酱",
        "米粉",
        "米汤",
        "米酒",
        "米糠油",
        "米淀粉",
        "米浆粉",
        "玉米",
        "花生米",
        "鸡头米",
        "鸡米花",
        "虾米",
        "米其林酱",
        "南豆腐乳",
        "南豆腐酱",
        "南豆腐粉",
        "南豆腐调味料",
        "日本豆腐",
    ],
)
def test_suffixes_other_foods_and_compounds_cannot_supply_rice_or_tofu(food: str) -> None:
    assert not food_families(dish("米饭南豆腐", [food]))
    assert not culinary_food_focus(dish("米饭南豆腐", [food])).families


@pytest.mark.parametrize(
    "name,foods,wanted",
    [
        ("基础煮燕麦饭", ["燕麦", "米", "水"], {"rice", "oats"}),
        ("燕麦饭", ["燕麦", "大米"], {"rice", "oats"}),
        ("杂粮饭", ["小米", "米"], {"rice", "millet"}),
        ("菜饭", ["米", "白菜"], {"rice"}),
        ("蘑菇烩饭", ["大米", "白蘑菇"], {"rice"}),
        ("糙米饭", ["米", "水"], {"rice"}),
        ("清蒸南豆腐汤", ["南豆腐", "盐"], {"tofu"}),
    ],
)
def test_declared_grains_in_rice_dish_and_named_tofu_soup_have_source_focus(
    name: str, foods: list[str], wanted: set[str]
) -> None:
    record = dish(name, foods, "soup" if name.endswith("汤") else "staple")
    assert culinary_food_focus(record).families == frozenset(wanted)


@pytest.mark.parametrize(
    "name,foods,role",
    [
        ("燕麦饭", ["水", "盐"], "staple"),
        ("米饭", ["米醋", "米酱"], "staple"),
        ("南豆腐汤", ["南豆腐调味料"], "soup"),
        ("下饭", ["米", "燕麦"], "staple"),
        ("好吃下饭", ["米", "燕麦"], "staple"),
        ("成菜甲", ["米", "燕麦"], "staple"),
        ("饭菜拼盘", ["米", "燕麦"], "staple"),
        ("家常汤", ["南豆腐"], "soup"),
        ("燕麦饭", ["鸡肉", "米", "燕麦"], "protein"),
        ("燕麦饭", ["米", "燕麦"], "dessert"),
        ("南瓜蛋糕", ["南瓜", "面粉", "白糖"], "staple"),
    ],
)
def test_title_role_or_food_alone_does_not_establish_whole_dish_focus(
    name: str, foods: list[str], role: str
) -> None:
    assert not culinary_food_focus(dish(name, foods, role)).families


def test_a_claimed_oat_title_does_not_invent_oats_or_whole_grain_identity() -> None:
    record = dish("燕麦饭", ["米", "水"])
    assert culinary_food_focus(record).families == frozenset({"rice"})
    record.name = "南豆腐汤"
    record.categories = ["soup"]
    assert not culinary_food_focus(record).families


def test_named_protein_role_cannot_gain_grain_focus_from_a_rice_suffix() -> None:
    record = dish("鸡肉燕麦饭", ["鸡肉", "米", "燕麦"], "protein")
    assert culinary_food_focus(record).families == frozenset({"chicken"})


def test_source_roles_preparation_mutations_and_cache_are_rechecked() -> None:
    record = dish("燕麦饭", ["米", "燕麦"])
    assert culinary_food_focus(record).families == frozenset({"rice", "oats"})
    record.categories = ["staple", "vegetable"]
    assert not culinary_food_focus(record).families
    record.categories = ["staple"]
    record.steps = "清洗食材，备好备用。"
    assert not culinary_food_focus(record).families
    record.steps = "食材加水蒸熟后装盘。"
    record.eligible = False
    assert not culinary_food_focus(record).families
    record.eligible = True
    record.ingredients = [Ingredient(name="米醋", raw="米醋")]
    assert not food_families(record)
    assert not culinary_food_focus(record).families
    record.ingredients = [Ingredient(name="南豆腐", raw="南豆腐")]
    record.name, record.categories = "南豆腐汤", ["soup"]
    assert food_families(record) == frozenset({"tofu"})
    assert culinary_food_focus(record).families == frozenset({"tofu"})


def test_evidence_does_not_rewrite_source_health_scores_or_hard_constraints() -> None:
    record = dish("南豆腐汤", ["南豆腐", "盐", "虾"], "soup")
    rules = RuleEngine()
    constraints = Constraints(no_spicy=True, health_goals=["降压", "护心"])
    before = record.model_dump()
    scores = rules.soft_goal_scores(record, constraints)
    assert food_families(record) == frozenset({"tofu"})
    assert culinary_food_focus(record).families == frozenset({"tofu"})
    assert record.model_dump() == before
    assert rules.soft_goal_scores(record, constraints) == scores
    assert not rules.evaluate(record, Constraints(allergies=["虾"])).allowed
    record.ingredients.append(Ingredient(name="小米辣", raw="小米辣"))
    record.raw_ingredients += "、小米辣"
    assert not rules.evaluate(record, constraints).allowed


def test_same_tofu_dish_cannot_win_by_missing_its_southern_alias() -> None:
    menu = [dish("豆腐汤一", ["豆腐"], "soup"), dish("豆腐汤二", ["嫩豆腐"], "soup")]
    disguised = dish("南豆腐汤", ["南豆腐"], "soup")
    pool = [*menu, disguised]
    result = repair_menu_variety(
        menu,
        pool,
        Constraints(dish_count=2, soup_count=2),
        goal_scores={r.recipe_id: () for r in pool},
        rule_scores={r.recipe_id: 0 for r in pool},
        relevance={r.recipe_id: 0.0 for r in pool},
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, term: False,
        family_evidence={r.recipe_id: culinary_food_focus(r).families for r in pool},
    )
    assert result.recipes == menu


def test_default_planner_and_plain_continue_do_not_enable_variety_by_normalization() -> None:
    menu = [dish("燕麦饭一", ["米", "燕麦"]), dish("燕麦饭二", ["米", "燕麦"])]
    alternative = dish("小米饭", ["小米"])
    planner = MenuPlanner(RuleEngine())
    constraints = Constraints(dish_count=2)
    result = planner.plan([*menu, alternative], constraints, current=menu)
    assert result.recipes == menu
    assert (
        planner.plan(
            [*menu, alternative],
            constraints,
            current=menu,
            experiment_menu_variety="culinary_focus_guarded",
            recheck_soft_preferences=False,
        ).recipes
        == menu
    )
