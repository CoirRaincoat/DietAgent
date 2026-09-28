"""Choose safe, varied same-role alternatives for a menu slot."""

import hashlib
from collections.abc import Sequence

from app.agent.menu_balance import balance_rank
from app.domain.models import Recipe
from app.rules.engine import compact


def replacement_candidates(
    menu: Sequence[Recipe],
    safe_recipes: Sequence[Recipe],
    session_id: str,
    limit: int = 2,
) -> list[Recipe]:
    """Rank slot-one alternatives by menu fit, method variety, then session tie-break.

    The session tie-break rotates equally suitable options across conversations
    while keeping retries within one conversation stable.
    """
    if not menu or limit <= 0:
        return []
    used_ids = {recipe.recipe_id for recipe in menu}
    used_names = {compact(recipe.name) for recipe in menu}
    target_categories = set(menu[0].categories)
    pool: list[Recipe] = []
    seen_pool_names = set(used_names)
    # The caller has already sorted safe recipes by retrieval relevance. A
    # bounded pool keeps optional suggestions from delaying the first token.
    for recipe in safe_recipes:
        name = compact(recipe.name)
        if (
            not recipe.eligible
            or recipe.recipe_id in used_ids
            or name in seen_pool_names
            or set(recipe.categories) != target_categories
        ):
            continue
        pool.append(recipe)
        seen_pool_names.add(name)
        if len(pool) == 64:
            break
    fit_by_id = {
        recipe.recipe_id: balance_rank([recipe, *menu[1:]], len(menu))
        for recipe in pool
    }
    tie_by_id = {
        recipe.recipe_id: hashlib.sha256(
            f"{session_id}:{recipe.recipe_id}".encode("utf-8")
        ).hexdigest()
        for recipe in pool
    }
    options: list[Recipe] = []
    seen_names = set(used_names)
    while pool and len(options) < limit:
        chosen_methods = {method for recipe in options for method in recipe.methods}

        def rank(recipe: Recipe) -> tuple[tuple[int, ...], int, str]:
            novelty = len(set(recipe.methods) - chosen_methods)
            return fit_by_id[recipe.recipe_id], novelty, tie_by_id[recipe.recipe_id]

        candidate = max(
            (recipe for recipe in pool if compact(recipe.name) not in seen_names),
            key=rank,
            default=None,
        )
        if candidate is None:
            break
        options.append(candidate)
        seen_names.add(compact(candidate.name))
        pool.remove(candidate)
    return options
