"""Report-only observations derived from verified source recipes."""

import pytest

from app.domain.models import Ingredient, Recipe
from evaluation.menu_quality import menu_quality_snapshot, summarize_menu_quality


def _recipe(
    recipe_id: str,
    *,
    categories: list[str],
    methods: list[str],
    ingredients: list[str],
    name: str = "测试菜",
    steps: str = "煮熟装盘。",
) -> Recipe:
    return Recipe(
        recipe_id=recipe_id,
        name=name,
        raw_ingredients="、".join(ingredients),
        steps=steps,
        ingredients=[Ingredient(raw=value, name=value) for value in ingredients],
        categories=categories,
        methods=methods,
        source_row=int(recipe_id),
        fingerprint=f"source-{recipe_id}",
    )


@pytest.fixture
def catalog() -> dict[str, Recipe]:
    return {
        "1": _recipe("1", categories=["vegetable"], methods=["炒"], ingredients=["鸡蛋", "盐"], steps="炒熟装盘。"),
        "2": _recipe("2", categories=["protein"], methods=["蒸"], ingredients=["鸡蛋", "葱"], steps="蒸熟装盘。"),
        "3": _recipe("3", categories=["staple"], methods=["煮"], ingredients=["米饭"]),
    }


def test_menu_quality_uses_source_recipes_and_pairwise_exact_ingredients(catalog) -> None:
    observation = menu_quality_snapshot(["1", "2", "3"], catalog)

    assert observation == {
        "status": "available",
        "dish_count": 3,
        "category_counts": {"vegetable": 1, "protein": 1, "staple": 1, "soup": 0},
        "role_coverage": 3,
        "method_count": 3,
        "method_evidence_version": "source-finishing-method-v5",
        "method_known_dishes": 3,
        "method_unknown_dishes": 0,
        "temperature_counts": {"hot": 3, "cold": 0, "unknown": 0},
        "possible_pairs": 3,
        "comparable_pairs": 3,
        "ingredient_overlap_mean": 0.1111,
        "ingredient_overlap_max": 0.3333,
    }


def test_single_dish_has_no_comparable_pair_instead_of_zero(catalog) -> None:
    observation = menu_quality_snapshot(["1"], catalog)

    assert observation["status"] == "available"
    assert observation["possible_pairs"] == 0
    assert observation["ingredient_overlap_mean"] is None
    assert observation["ingredient_overlap_max"] is None


@pytest.mark.parametrize(
    ("ids", "catalog_missing", "reason"),
    [
        ([], False, "empty_menu"),
        (["1"], True, "catalog_unavailable"),
        (["1", "1"], False, "duplicate_recipe_id"),
        (["unknown"], False, "recipe_not_in_catalog"),
    ],
)
def test_unverifiable_menu_does_not_emit_quality_numbers(
    catalog, ids, catalog_missing, reason
) -> None:
    observation = menu_quality_snapshot(ids, None if catalog_missing else catalog)

    assert observation == {"status": "unavailable", "reason": reason}


def test_summary_excludes_unavailable_and_non_applicable_menus(catalog) -> None:
    measured = menu_quality_snapshot(["1", "2", "3"], catalog)
    single = menu_quality_snapshot(["1"], catalog)
    cases = [
        {"turns": [{"menu_quality": measured}, {"menu_quality": single}]},
        {"turns": [{"menu_quality": {"status": "unavailable", "reason": "empty_menu"}}]},
        {"turns": [{"menu_quality": {"status": "not_applicable"}}]},
    ]

    summary = summarize_menu_quality(cases)

    assert summary["menus_measured"] == 2
    assert summary["menus_unavailable"] == 1
    assert summary["unavailable_reasons"] == {"empty_menu": 1}
    assert summary["menus_with_comparable_pairs"] == 1
    assert summary["temperature_counts"] == {"hot": 4, "cold": 0, "unknown": 0}
    assert summary["mean_role_coverage"] == 2.0
    assert summary["mean_method_count"] == 2.0
    assert summary["mean_ingredient_overlap"] == 0.1111


def test_no_observation_is_not_a_zero_quality_score() -> None:
    summary = summarize_menu_quality([{"turns": []}])

    assert summary["status"] == "not_run"
    assert summary["mean_role_coverage"] is None
    assert summary["mean_ingredient_overlap"] is None


def test_all_unavailable_menus_are_distinct_from_not_run() -> None:
    summary = summarize_menu_quality(
        [
            {
                "turns": [
                    {
                        "menu_quality": {
                            "status": "unavailable",
                            "reason": "validation_not_observed",
                        }
                    }
                ]
            }
        ]
    )

    assert summary["status"] == "unavailable"
    assert summary["menus_unavailable"] == 1
    assert summary["mean_role_coverage"] is None
