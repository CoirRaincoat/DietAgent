"""Source-derived menu diversity observations, separate from acceptance scores."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations
from statistics import mean
from typing import Any

from app.agent.menu_balance import serving_temperature
from app.domain.cooking_methods import METHOD_VERSION, main_cooking_methods
from app.domain.models import Recipe
from app.rules.engine import compact

ROLES = ("vegetable", "protein", "staple", "soup")
TEMPERATURES = ("hot", "cold", "unknown")
QUALITY_VERSION = "source-menu-diversity-v5"


def _ingredient_names(recipe: Recipe) -> set[str]:
    """Return exact normalized ingredient names, without inferred aliases."""
    return {name for item in recipe.ingredients if (name := compact(item.name))}


def menu_quality_snapshot(
    recipe_ids: Sequence[str], catalog: Mapping[str, Recipe] | None
) -> dict[str, Any]:
    """Measure one catalog-backed menu without trusting displayed recipe fields.

    Args:
        recipe_ids: Ordered recipe IDs from the observed menu.
        catalog: Source catalog used to validate and describe those IDs.

    Returns:
        Numeric, source-derived observations or a fixed unavailable reason.
        Ingredient overlap is exact-name Jaccard similarity, not a nutrition
        measure or proof that dishes are genuinely different.
    """
    if not recipe_ids:
        return {"status": "unavailable", "reason": "empty_menu"}
    if catalog is None:
        return {"status": "unavailable", "reason": "catalog_unavailable"}
    if len(recipe_ids) != len(set(recipe_ids)):
        return {"status": "unavailable", "reason": "duplicate_recipe_id"}
    if any(recipe_id not in catalog for recipe_id in recipe_ids):
        return {"status": "unavailable", "reason": "recipe_not_in_catalog"}

    recipes = [catalog[recipe_id] for recipe_id in recipe_ids]
    categories = Counter(
        category for recipe in recipes for category in set(recipe.categories) if category in ROLES
    )
    source_methods = [main_cooking_methods(recipe) for recipe in recipes]
    methods = {method for recorded in source_methods for method in recorded}
    temperatures = Counter(serving_temperature(recipe) for recipe in recipes)
    ingredient_sets = [_ingredient_names(recipe) for recipe in recipes]
    overlaps = [
        len(left & right) / len(left | right)
        for left, right in combinations(ingredient_sets, 2)
        if left and right
    ]
    return {
        "status": "available",
        "dish_count": len(recipes),
        "category_counts": {key: categories[key] for key in ROLES},
        "role_coverage": sum(categories[key] > 0 for key in ROLES[:3]),
        "method_count": len(methods),
        "method_evidence_version": METHOD_VERSION,
        "method_known_dishes": sum(bool(recorded) for recorded in source_methods),
        "method_unknown_dishes": sum(not recorded for recorded in source_methods),
        "temperature_counts": {key: temperatures[key] for key in TEMPERATURES},
        "possible_pairs": len(recipes) * (len(recipes) - 1) // 2,
        "comparable_pairs": len(overlaps),
        "ingredient_overlap_mean": round(mean(overlaps), 4) if overlaps else None,
        "ingredient_overlap_max": round(max(overlaps), 4) if overlaps else None,
    }


def summarize_menu_quality(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate observed menus without adding points to the rubric score.

    Each menu contributes equally to the means. Menus without a comparable
    ingredient pair are excluded only from ingredient-overlap means.
    """
    observations = [
        turn["menu_quality"]
        for case in cases
        for turn in case.get("turns", [])
        if isinstance(turn.get("menu_quality"), dict)
    ]
    available = [item for item in observations if item.get("status") == "available"]
    unavailable = [item for item in observations if item.get("status") == "unavailable"]
    method_coverage = [
        item
        for item in available
        if item.get("method_evidence_version") == METHOD_VERSION
        and "method_known_dishes" in item
        and "method_unknown_dishes" in item
    ]
    temperatures = Counter({key: 0 for key in TEMPERATURES})
    for item in available:
        temperatures.update(item["temperature_counts"])
    overlaps = [
        item["ingredient_overlap_mean"]
        for item in available
        if item.get("ingredient_overlap_mean") is not None
    ]
    worst_pairs = [
        item["ingredient_overlap_max"]
        for item in available
        if item.get("ingredient_overlap_max") is not None
    ]
    return {
        "version": QUALITY_VERSION,
        "status": "available" if available else "unavailable" if unavailable else "not_run",
        "menus_measured": len(available),
        "menus_unavailable": len(unavailable),
        "method_evidence_version": METHOD_VERSION,
        "method_coverage_menus": len(method_coverage),
        "method_known_dishes": sum(item["method_known_dishes"] for item in method_coverage)
        if method_coverage
        else None,
        "method_unknown_dishes": sum(item["method_unknown_dishes"] for item in method_coverage)
        if method_coverage
        else None,
        "unavailable_reasons": dict(
            sorted(Counter(item["reason"] for item in unavailable).items())
        ),
        "menus_with_comparable_pairs": len(overlaps),
        "temperature_counts": {key: temperatures[key] for key in TEMPERATURES},
        "mean_role_coverage": round(mean(item["role_coverage"] for item in available), 3)
        if available
        else None,
        "mean_method_count": round(mean(item["method_count"] for item in available), 3)
        if available
        else None,
        "mean_ingredient_overlap": round(mean(overlaps), 4) if overlaps else None,
        "mean_worst_pair_overlap": round(mean(worst_pairs), 4) if worst_pairs else None,
    }
