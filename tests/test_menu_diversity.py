"""Source-only similarity is a deterministic, uncertainty-aware tie-break."""

from app.agent.menu_diversity import menu_similarity_penalty
from app.domain.models import Ingredient, Recipe


def recipe(recipe_id: str, ingredients: list[str], method: str) -> Recipe:
    """Build one source-like recipe without network or database access."""
    return Recipe(
        recipe_id=recipe_id,
        name=recipe_id,
        raw_ingredients="、".join(ingredients),
        steps="烹饪后装盘。",
        ingredients=[Ingredient(raw=name, name=name) for name in ingredients],
        methods=[method] if method else [],
        source_row=1,
        fingerprint=recipe_id,
    )


def test_no_selected_dish_has_no_diversity_penalty() -> None:
    candidate = recipe("candidate", ["鸡蛋"], "蒸")

    assert menu_similarity_penalty(candidate, []) == (0.0, 0, 0.0, 0.0)


def test_different_main_ingredient_beats_same_main_with_different_method() -> None:
    selected = recipe("selected", ["鸡蛋"], "蒸")
    repeated_food = recipe("repeated", ["鸡蛋", "青菜"], "炒")
    repeated_method = recipe("other", ["西兰花"], "蒸")

    assert menu_similarity_penalty(repeated_method, [selected]) < menu_similarity_penalty(
        repeated_food, [selected]
    )


def test_different_method_breaks_tie_after_ingredient_match() -> None:
    selected = recipe("selected", ["鸡蛋"], "蒸")
    same_method = recipe("same", ["西兰花"], "蒸")
    different_method = recipe("different", ["西兰花"], "炒")

    assert menu_similarity_penalty(different_method, [selected]) < menu_similarity_penalty(
        same_method, [selected]
    )


def test_missing_metadata_is_not_treated_as_perfect_novelty() -> None:
    selected = recipe("selected", ["鸡蛋"], "蒸")
    no_ingredients = recipe("missing", [], "炒")
    no_method = recipe("no-method", ["西兰花"], "")

    assert menu_similarity_penalty(no_ingredients, [selected])[0] == 1.0
    assert menu_similarity_penalty(no_method, [selected])[2] == 1.0
