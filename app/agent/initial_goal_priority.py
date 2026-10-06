"""Offline initial-ranking experiment; source preferences are not clinical scores."""

from collections.abc import Mapping, Sequence

from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Recipe


def source_goal_frontier(
    candidates: Sequence[Recipe], goal_scores: Mapping[str, tuple[int, ...]]
) -> list[Recipe]:
    """Retain nondominated per-goal/source-knowledge vectors in original order.

    The caller must first screen safety, culinary roles, meal context and any
    explicitly authorized method priority. Equal and incomparable vectors stay;
    this does not invent goal weights or guarantee a better final menu. Known
    finishing evidence is a separate coordinate, so a better configuration
    proxy alone cannot eliminate a source-known candidate for an unknown one.

    Empty scores are an identity operation. Otherwise every candidate needs a
    same-width vector. Compressing duplicates bounds comparison work by the
    number of distinct vectors U, not the square of the recipe count N:
    O(N * D + U**2 * D), where D is the requested goal count plus knowledge.
    """
    pool = list(candidates)
    if not pool or not goal_scores:
        return pool
    vectors = [goal_scores[recipe.recipe_id] for recipe in pool]
    if len({len(vector) for vector in vectors}) != 1:
        raise ValueError("initial goal vectors must have the same width")
    if not vectors[0]:
        return pool
    with_knowledge = [
        (*vector, int(bool(main_cooking_methods(recipe)))) for recipe, vector in zip(pool, vectors)
    ]
    unique = set(with_knowledge)
    frontier = {
        vector
        for vector in unique
        if not any(
            other != vector and all(new >= old for new, old in zip(other, vector))
            for other in unique
        )
    }
    return [recipe for recipe, vector in zip(pool, with_knowledge) if vector in frontier]
