"""Lossless-content recipe presentation without generated images or estimates."""

import re

from app.domain.cards import CookingStep, DishCard, RecipeProvenance
from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Recipe
from app.domain.recipe_origin import display_source_row, generator_version, is_generated_recipe
from app.infrastructure.data import LABEL_ALLOWLIST

# Culinary categories only. Protein evidence belongs in the separately checked
# nutrition object; title-derived classes cannot become nutrient badges.
_CATEGORY_BADGES = {
    "vegetable": "蔬菜类",
    "staple": "主食类",
    "soup": "汤羹类",
    "dessert": "甜品类",
    "drink": "饮品类",
    "component": "加工组件",
}
_METHOD_ALLOWLIST = {"蒸", "煮", "炖", "炒", "烤", "煎", "炸", "焖", "烧", "焯", "拌", "榨汁"}


def build_card(recipe: Recipe) -> DishCard:
    """Display source metadata; unsupported display facts stay null."""
    categories = [_CATEGORY_BADGES[value] for value in recipe.categories
                  if value in _CATEGORY_BADGES]
    methods = [value for value in main_cooking_methods(recipe) if value in _METHOD_ALLOWLIST]
    labels = [value for value in recipe.labels if value in LABEL_ALLOWLIST]
    badges = list(dict.fromkeys([*categories, *methods, *labels]))
    if is_generated_recipe(recipe):
        badges.insert(0, "新生成方案·待试做")
    subtitle = " · ".join(list(dict.fromkeys([*categories, *methods]))[:3])
    return DishCard(
        title=recipe.name,
        subtitle=subtitle or "查看食材与做法",
        badges=badges,
    )


def split_cooking_steps(steps: str) -> list[CookingStep]:
    """Split only physical newlines, retaining all nonblank source text.

    Original step numbers, cautions, punctuation and indentation stay intact.
    Empty layout lines are omitted; the API also retains the full raw steps.
    A paragraph without line breaks remains a single item, not guessed steps.
    """
    lines = [line for line in re.split(r"\r\n|\r|\n", steps) if line.strip()]
    return [CookingStep(number=index, description=line) for index, line in enumerate(lines, 1)]


def recipe_provenance(recipe: Recipe) -> RecipeProvenance:
    return RecipeProvenance(
        recipe_id=recipe.recipe_id,
        source_row=display_source_row(recipe),
        fingerprint=recipe.fingerprint,
        origin="generated" if is_generated_recipe(recipe) else "catalog",
        generator_version=generator_version(recipe),
    )
