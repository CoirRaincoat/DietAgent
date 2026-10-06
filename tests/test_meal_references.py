"""Authored, source-bound meal references; not independent suitability labels."""

import csv
from functools import lru_cache
from pathlib import Path

import pytest

from app.agent.meal_context import repair_meal_context
from app.agent.planner import MenuPlanner
from app.domain.matching_tags import matching_tags
from app.domain.meal_context import meal_context_warnings, meal_cost, meal_fit, source_meals
from app.domain.meal_references import MEAL_REFERENCES, meal_reference
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine


@lru_cache(maxsize=1)
def source_catalog() -> dict[int, Recipe]:
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as handle:
        return {r.source_row: r for r in normalize_recipes(csv.DictReader(handle)).values()}


def dish(name: str, labels: str = "", foods: str = "大米100克；水500克") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": "蒸熟装盘。",
                        "label": labels,
                    }
                ]
            ).values()
        )
    )


def test_grounded_breakfast_reference_is_not_an_original_label() -> None:
    recipe = source_catalog()[1110]
    assert not source_meals(recipe)
    assert meal_fit(recipe, "早餐") == "suggested"
    assert meal_fit(recipe, "晚餐") == "unknown"
    assert meal_cost(recipe, "早餐") < meal_cost(source_catalog()[1657], "早餐")
    warnings = "\n".join(meal_context_warnings([recipe], "早餐"))
    assert "助手餐次参考" in warnings and "尚未核验" in warnings


def test_retrieval_and_real_planner_consume_reference_without_certification() -> None:
    plain, suggested = source_catalog()[1657], source_catalog()[1110]
    constraints = Constraints(meal_type="早餐", dish_count=1)
    assert KeywordRetriever([plain, suggested]).search([], constraints)[0] == suggested
    result = MenuPlanner(RuleEngine()).plan([plain, suggested], constraints, current=[plain])
    assert result.failure is None
    assert result.recipes == [suggested]
    assert any("助手餐次参考" in warning for warning in result.warnings)


def test_explicit_continue_does_not_repair_accepted_unknown_meal() -> None:
    plain, suggested = source_catalog()[1657], source_catalog()[1110]
    result = MenuPlanner(RuleEngine()).plan(
        [plain, suggested],
        Constraints(meal_type="早餐", dish_count=1),
        current=[plain],
        recheck_soft_preferences=False,
    )
    assert result.recipes == [plain]


def test_meal_repair_preserves_existing_flavor_and_scene() -> None:
    old = dish("家常蒜香米饭", "早餐、家常、蒜香")
    new = dish("蒸饭", "晚餐")
    repair = repair_meal_context(
        [old],
        [new],
        Constraints(meal_type="晚餐", dish_count=1, preferences=["家常", "蒜香"]),
        scores={},
        order={},
        food_matches=RuleEngine().food_matches,
    )
    assert repair.recipes == [old]


@pytest.mark.parametrize("row,meal", [(212, "晚餐"), (365, "早餐"), (1987, "早餐")])
def test_references_cannot_waive_spicy_or_allergen_constraints(row: int, meal: str) -> None:
    recipe = source_catalog()[row]
    constraints = Constraints(
        meal_type=meal,
        dish_count=1,
        no_spicy=row == 212,
        allergies=["核桃"] if row == 365 else ["花生"] if row == 1987 else [],
    )
    result = MenuPlanner(RuleEngine()).plan([recipe], constraints)
    assert result.failure is not None and result.recipes == []


@pytest.mark.parametrize("row", [64, 212, 365, 570, 744, 865, 866, 1110, 1167, 1328, 1515, 1987])
def test_each_finite_reference_binds_full_record_and_literal_source_excerpts(row: int) -> None:
    recipe = source_catalog()[row]
    before = recipe.model_dump(mode="json")
    reference = meal_reference(recipe)
    assert reference is not None
    assert not source_meals(recipe)
    assert reference.ingredient_excerpt in recipe.raw_ingredients
    assert reference.preparation_excerpt in recipe.steps
    tags = matching_tags(recipe)
    assert tags.meals == () and "meal" in tags.unknown_axes
    assert tuple(t.tag for t in tags.meal_references) == reference.meals
    assert all(t.origin == "assistant_review_reference" for t in tags.meal_references)
    assert all(meal_fit(recipe, meal) == "suggested" for meal in reference.meals)
    assert before == recipe.model_dump(mode="json")


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "另一道粥"),
        ("steps", "改用炒锅做。"),
        ("raw_ingredients", "大米100克"),
        ("raw_label", "晚餐"),
        ("source_row", 1111),
        ("fingerprint", "forged"),
        ("meal_types", ["晚餐"]),
        ("categories", ["protein"]),
        ("quality_flags", []),
    ],
)
def test_changed_record_even_with_copied_id_is_never_promoted(field: str, value: object) -> None:
    altered = source_catalog()[1110].model_copy(update={field: value}, deep=True)
    assert meal_reference(altered) is None
    assert "未采用" in "\n".join(meal_context_warnings([altered], "早餐"))


def test_source_labels_remain_stronger_and_unknown_not_excluded() -> None:
    suggested = source_catalog()[1110]
    known = dish("早餐蒸饭", "早餐")
    unknown = source_catalog()[1657]
    constraints = Constraints(meal_type="早餐", dish_count=1)
    result = MenuPlanner(RuleEngine()).plan(
        [suggested, known], constraints, query_terms=[suggested.name]
    )
    assert result.recipes == [known]
    assert meal_cost(known, "早餐") < meal_cost(suggested, "早餐")
    result = MenuPlanner(RuleEngine()).plan([unknown], constraints)
    assert result.recipes == [unknown]
    assert any("尚未核验" in warning for warning in result.warnings)


@pytest.mark.parametrize("row", [20, 233, 404, 467, 559, 651, 879, 1298, 1463, 1514])
def test_unreviewed_components_or_ambiguous_assembly_are_not_blanket_labeled(row: int) -> None:
    recipe = source_catalog()[row]
    assert meal_reference(recipe) is None
    assert meal_fit(recipe, "午餐") == "unknown"


def test_reference_registry_cannot_be_mutated_or_extended_by_a_similar_title() -> None:
    with pytest.raises(TypeError):
        MEAL_REFERENCES["fake"] = MEAL_REFERENCES[source_catalog()[1110].recipe_id]  # type: ignore[index]
    assert (
        meal_reference(dish("南瓜二米粥", foods="大米30克；小米30克；南瓜100克；水800克")) is None
    )


@pytest.mark.parametrize("scores", [None, {}, {"old": (3, 0), "new": (2, 100)}])
def test_context_repair_cannot_trade_off_goals_or_use_missing_vectors(scores: object) -> None:
    old, new = dish("早餐蒸饭", "早餐"), dish("晚餐蒸饭", "晚餐")
    mapped = {old.recipe_id: (3, 0), new.recipe_id: (2, 100)} if scores else scores
    repair = repair_meal_context(
        [old],
        [new],
        Constraints(meal_type="晚餐", dish_count=1, health_goals=["降压", "护心"]),
        scores={new.recipe_id: 100},
        order={},
        food_matches=RuleEngine().food_matches,
        goal_scores=mapped,  # type: ignore[arg-type]
    )
    assert repair.recipes == [old]


def test_local_context_repair_keeps_other_slots_and_food_coverage() -> None:
    old, new = source_catalog()[1657], source_catalog()[1110]
    retained = dish("早餐蒸饭", "午餐")
    constraints = Constraints(meal_type="早餐", dish_count=2)
    repair = repair_meal_context(
        [retained, old],
        [new],
        constraints,
        replace_slot=2,
        scores={},
        order={},
        food_matches=RuleEngine().food_matches,
    )
    assert repair.recipes == [retained, new]
    constrained = constraints.model_copy(update={"preferred_ingredients": ["冬瓜"]})
    repair = repair_meal_context(
        [retained, old],
        [new],
        constrained,
        replace_slot=2,
        scores={},
        order={},
        food_matches=RuleEngine().food_matches,
    )
    assert repair.recipes == [retained, old]


def test_meal_reference_does_not_make_pork_fat_vegetable_a_vegetarian_dish() -> None:
    result = MenuPlanner(RuleEngine()).plan(
        [source_catalog()[64]],
        Constraints(
            meal_type="晚餐",
            dish_count=1,
            meat_dish_count=0,
            vegetarian_dish_count=1,
        ),
    )
    assert result.failure is not None and not result.recipes


def test_context_repair_rejects_bad_slot_and_conflicting_identities() -> None:
    old, new = source_catalog()[1657], source_catalog()[1110]
    with pytest.raises(ValueError, match="slot"):
        repair_meal_context(
            [old],
            [new],
            Constraints(),
            scores={},
            order={},
            food_matches=RuleEngine().food_matches,
            replace_slot=0,
        )
    with pytest.raises(ValueError, match="identity"):
        repair_meal_context(
            [old],
            [old.model_copy(update={"steps": "changed"})],
            Constraints(),
            scores={},
            order={},
            food_matches=RuleEngine().food_matches,
        )
