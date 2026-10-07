"""Authored plum beverage and savory contrasts; never real users or live APIs."""

from app.domain.meal_roles import is_main_meal_recipe
from app.infrastructure.data import normalize_recipes
from evaluation.main_meal_oracle import main_meal_findings


def test_sweet_plum_beverage_cannot_fill_ordinary_meal_soup_slot() -> None:
    recipe = next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "宫廷酸梅汤",
                        "食材清单": "乌梅30克；水1000克；冰糖50克",
                        "烹饪步骤": "加水熬煮，过滤取汤汁，冷藏后饮用。",
                        "label": "早餐、下午茶",
                    }
                ]
            ).values()
        )
    )
    assert is_main_meal_recipe(recipe) is False
    assert recipe.categories == ["drink"]
    assert main_meal_findings(recipe.name, recipe.raw_ingredients, steps=recipe.steps)


def test_plum_named_savory_meat_is_not_banned_by_a_drink_substring() -> None:
    recipe = next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "酸梅汤炖排骨",
                        "食材清单": "乌梅30克；排骨300克；盐2克；水1000克；冰糖10克",
                        "烹饪步骤": "加入排骨炖熟后装盘。",
                        "label": "晚餐",
                    }
                ]
            ).values()
        )
    )
    assert is_main_meal_recipe(recipe) is True
    assert main_meal_findings(recipe.name, recipe.raw_ingredients, steps=recipe.steps) == []
