"""Handwritten reconstructions of observed source gaps, not official dialogues."""

import pytest

from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.diet import diet_reasons
from evaluation.main_meal_oracle import main_meal_findings


@pytest.mark.parametrize("egg", ["鸡蛋", "鸭蛋", "鹌鹑蛋"])
@pytest.mark.parametrize("savory", ["", "；盐2克", "；猪肉片50克"])
def test_egg_does_not_turn_sweet_silver_ear_soup_into_meal_soup(egg: str, savory: str) -> None:
    recipe = next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "银耳蛋汤",
                        "食材清单": f"银耳20克；冰糖40克；水1000克；{egg}1颗{savory}",
                        "烹饪步骤": "加水熬煮后加入蛋液煮熟即可食用。",
                        "label": "晚餐",
                    }
                ]
            ).values()
        )
    )
    assert is_main_meal_recipe(recipe) is bool(savory)
    assert bool(main_meal_findings(recipe.name, recipe.raw_ingredients)) is not bool(savory)


def test_optional_unspecified_dip_is_not_verified_whole_meal_vegetarian() -> None:
    recipe = next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "玫瑰卷",
                        "食材清单": "面粉100克；酵母2克；水80克",
                        "烹饪步骤": "揉面发酵后蒸熟。根据自己爱好加蘸料。",
                        "label": "早餐",
                    }
                ]
            ).values()
        )
    )
    assert diet_reasons(recipe, Constraints(diet_mode="vegan"))
    assert diet_reasons(recipe, Constraints(diet_mode="ovo_lacto_vegetarian"))
