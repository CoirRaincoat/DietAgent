"""Same-role replacement choices stay safe, distinct and varied."""

from app.agent.suggestions import replacement_candidates
from app.domain.models import Ingredient, Recipe


def recipe(key: str, name: str, method: str = "蒸") -> Recipe:
    """Create a source-backed menu candidate with one culinary role."""
    return Recipe(
        recipe_id=key,
        name=name,
        raw_ingredients="鸡蛋",
        ingredients=[Ingredient(raw="鸡蛋", name="鸡蛋")],
        steps="鸡蛋蒸熟后装盘。",
        source_row=1,
        fingerprint=key,
        categories=["protein"],
        methods=[method],
    )


def test_alternatives_never_repeat_menu_or_duplicate_dish_names() -> None:
    original = recipe("original", "蒸鸡蛋")
    same_name = recipe("duplicate", "蒸鸡蛋")
    excluded = recipe("blocked", "鸡蛋羹")
    excluded.eligible = False
    other_role = recipe("staple", "米饭")
    other_role.categories = ["staple"]
    candidates = [
        same_name,
        excluded,
        other_role,
        recipe("a", "炒鸡蛋", "炒"),
        recipe("b", "煮鸡蛋", "煮"),
    ]

    result = replacement_candidates([original], candidates, "session-1")

    assert {item.recipe_id for item in result} == {"a", "b"}
    assert len({item.name for item in result}) == 2
    assert replacement_candidates([original], candidates, "session-1") == result


def test_equal_fit_candidates_rotate_across_sessions() -> None:
    original = recipe("original", "蒸鸡蛋")
    candidates = [recipe(f"r{index}", f"鸡蛋菜{index}") for index in range(8)]

    first_choices = {
        replacement_candidates([original], candidates, f"session-{index}")[0].recipe_id
        for index in range(12)
    }

    assert len(first_choices) >= 3
