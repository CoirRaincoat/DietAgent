"""Finite positive meal-group evidence, never an allergy or nutrition claim.

Match groups only for stated preferences. A leafy dish needs a named, whole
declared vegetable and a live vegetable role; a garnish or soup is not credited
as a separate leafy dish. Existing source classifiers provide the staple role.
"""

import re
from functools import lru_cache

from app.domain.dish_roles import primary_dish_role
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Recipe
from app.domain.protein_food_names import whole_food_pattern

VERSION = "source-menu-group-preferences-v2-vegetable-role"
GROUPS = frozenset({"主食", "蔬菜", "绿叶菜", "绿叶蔬菜"})
LEAFY_FOODS = (
    "菠菜", "油麦菜", "菜心", "小青菜", "小白菜", "生菜", "油菜", "上海青",
    "芥蓝", "芥兰", "空心菜", "茼蒿", "苋菜", "韭菜",
)
_LEAFY_PATTERNS = {food: whole_food_pattern((food,)) for food in LEAFY_FOODS}


def is_menu_group_preference(value: str) -> bool:
    return value.strip() in GROUPS


@lru_cache(maxsize=8192)
def _source_matches(name: str, foods: tuple[str, ...], steps: str, label: str, role: str, group: str) -> tuple[str, ...]:
    live_role = primary_dish_role(name, foods, steps, re.split(r"[、,，;；|\s]+", label))
    if live_role != role:
        return ()
    if role == "staple":
        return ("主食",)
    if role != "vegetable":
        return ()
    if group == "蔬菜":
        # A live, source-backed vegetable slot covers this culinary group.
        # An optional "配喜欢的蔬菜" in a protein's steps does not create one.
        # This is not proof of vegetable weight, servings or vegetarian diet.
        return ("蔬菜",)
    title = "".join(name.split()).split("配", 1)[0]
    for food in LEAFY_FOODS:
        for derivative in ("汁", "粉", "酱", "风味"):
            title = title.replace(food + derivative, "")
    return tuple(food for food, pattern in _LEAFY_PATTERNS.items()
                 if food in title and any(pattern.fullmatch("".join(value.split())) for value in foods))


def menu_group_matches(recipe: Recipe, value: str) -> list[str]:
    """Positive coverage only: not a guarantee of dominant mass or servings."""
    if (
        not is_menu_group_preference(value)
        or not recipe.eligible
        or "unparsed_ingredients" in recipe.quality_flags
        or not is_main_meal_recipe(recipe)
    ):
        return []
    role = "staple" if value.strip() == "主食" else "vegetable"
    if recipe.categories != [role]:
        return []
    return list(_source_matches(recipe.name, tuple(i.name for i in recipe.ingredients),
                                recipe.steps, recipe.raw_label, role, value.strip()))
