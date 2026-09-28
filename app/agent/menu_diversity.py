"""Conservative source-metadata tie-break for otherwise equally suitable dishes."""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher

from app.domain.models import Recipe
from app.rules.engine import compact

Similarity = tuple[float, int, float, float]


def _ingredients(recipe: Recipe) -> set[str]:
    """Return exact normalized source ingredient names without inferred aliases."""
    return {name for item in recipe.ingredients if (name := compact(item.name))}


def _pair_similarity(candidate: Recipe, selected: Recipe) -> Similarity:
    candidate_ingredients = _ingredients(candidate)
    selected_ingredients = _ingredients(selected)
    # Missing metadata must not masquerade as perfect diversity.
    ingredient_overlap = (
        len(candidate_ingredients & selected_ingredients)
        / len(candidate_ingredients | selected_ingredients)
        if candidate_ingredients and selected_ingredients
        else 1.0
    )
    candidate_primary = compact(candidate.ingredients[0].name) if candidate.ingredients else ""
    selected_primary = compact(selected.ingredients[0].name) if selected.ingredients else ""
    same_primary = int(bool(candidate_primary and candidate_primary == selected_primary))
    candidate_methods = set(candidate.methods)
    selected_methods = set(selected.methods)
    method_overlap = (
        len(candidate_methods & selected_methods) / len(candidate_methods | selected_methods)
        if candidate_methods and selected_methods
        else 1.0
    )
    name_overlap = SequenceMatcher(
        None, compact(candidate.name), compact(selected.name), autojunk=False
    ).ratio()
    return (
        round(ingredient_overlap, 4),
        same_primary,
        round(method_overlap, 4),
        round(name_overlap, 4),
    )


def menu_similarity_penalty(candidate: Recipe, selected: Sequence[Recipe]) -> Similarity:
    """Return the worst source-based similarity to an already chosen dish.

    The tuple is a lower-is-better tie-break only. It does not override hard
    eligibility, menu-role balance, explicit preference or query relevance.
    """
    return max(
        (_pair_similarity(candidate, recipe) for recipe in selected),
        default=(0.0, 0, 0.0, 0.0),
    )
