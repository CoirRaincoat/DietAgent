"""Ingredient-grounded qualitative nutrition tool, for one dish or a meal."""

from app.domain.models import Constraints, Recipe
from app.nutrition.models import MenuNutrition, RecipeNutrition
from app.nutrition.qualitative import analyze, analyze_menu, analyze_recipe


class NutritionAnalysisTool:
    def __call__(
        self, *, constraints: Constraints, recipe: Recipe | None = None,
        recipes: list[Recipe] | None = None, detailed: bool = False,
    ) -> list[str] | RecipeNutrition | MenuNutrition:
        if (recipe is None) == (recipes is None):
            raise ValueError("Provide exactly one recipe or a recipe list")
        if recipes is not None:
            return analyze_menu(recipes, constraints)
        return analyze_recipe(recipe, constraints) if detailed else analyze(recipe, constraints)
