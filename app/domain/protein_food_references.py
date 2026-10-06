"""Named, declared protein dishes, separate from mere ingredient presence.

Finite culinary references, not ingredient proportions, nutrition or a hard
request that every preferred food must become a separate entrée. Unknowns do
not acquire positive evidence. No recipe IDs or user/session facts enter this.
"""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache

from app.domain.dish_roles import primary_dish_role
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Recipe
from app.domain.protein_food_names import PROTEIN_FOOD_SPEC, named_declared_protein_foods

VERSION = "named-declared-protein-food-reference-v2-shared-vocabulary"


def supported_protein_foods(values: Iterable[str]) -> tuple[str, ...]:
    """Caller supplies canonical foods; only this finite reference axis applies."""
    return tuple(dict.fromkeys(value for value in values if value in PROTEIN_FOOD_SPEC))


@lru_cache(maxsize=8192)
def _references(name: str, foods: tuple[str, ...], steps: str, label: str) -> frozenset[str]:
    if primary_dish_role(name, foods, steps, re.split(r"[、,，;；|\s]+", label)) != "protein":
        return frozenset()
    return named_declared_protein_foods(name, foods)


def named_protein_foods(recipe: Recipe) -> frozenset[str]:
    """Source name + declared food + live protein role, never soup/binder credit.

    Multiple named foods may be referenced. This does not measure a dominant
    ingredient or require a meat-only dish. A source-unknown preparation or
    food remains outside this finite claim. Hard gates still belong to caller.
    """
    if (
        not recipe.eligible
        or recipe.categories != ["protein"]
        or "unparsed_ingredients" in recipe.quality_flags
        or not is_main_meal_recipe(recipe)
    ):
        return frozenset()
    return _references(
        recipe.name, tuple(i.name for i in recipe.ingredients), recipe.steps, recipe.raw_label
    )


def protein_food_mask(recipe: Recipe, requested: Sequence[str]) -> int:
    references = named_protein_foods(recipe)
    return sum(1 << i for i, food in enumerate(requested) if food in references)


def protein_food_coverage(menu: Sequence[Recipe], requested: Sequence[str]) -> int:
    value = 0
    for recipe in menu:
        value |= protein_food_mask(recipe, requested)
    return value
