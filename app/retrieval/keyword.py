"""Lexical retrieval with explicit label filtering and catalog fallback."""

from collections.abc import Iterable, Sequence

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine, contains_term


def recipe_relevance_score(
    recipe: Recipe,
    query_terms: Sequence[str],
    constraints: Constraints,
    rules: RuleEngine,
) -> float:
    """Score query and stated preferences identically in retrieval and planning.

    Args:
        recipe: Source recipe to score.
        query_terms: Terms extracted from the current request.
        constraints: Conversation-level soft preferences and meal type.
        rules: Shared food-alias resolver.

    Returns:
        A lexical soft score; never an eligibility or nutrition judgment.
    """
    terms = dict.fromkeys(
        item.strip() for item in [*query_terms, *constraints.preferred_ingredients] if item.strip()
    )
    value = 0.0
    for term in terms:
        synonyms = rules.aliases_for(term)
        if any(contains_term(recipe.name, alias) for alias in synonyms):
            value += 4
        if rules.food_matches(recipe, term):
            value += 3
        if any(contains_term(label, term) for label in recipe.labels):
            value += 1
    for preference in constraints.preferences:
        if any(contains_term(label, preference) for label in recipe.labels):
            value += 0.5
    if constraints.meal_type in recipe.meal_types:
        value += 0.5
    return value


class KeywordRetriever:
    def __init__(self, recipes: Iterable[Recipe]):
        self.recipes = list(recipes)
        self._rules = RuleEngine()

    def search(
        self,
        query_terms: list[str],
        constraints: Constraints,
        limit: int | None = None,
        *,
        required_labels: list[str] | None = None,
    ) -> list[Recipe]:
        """Rank by food, title and label matches without inventing recipes.

        No lexical hit is not a proof of infeasibility. Without an explicit
        label filter, limit=None preserves all eligible records for planning.
        required_labels requires all exact labels; no match remains empty.
        Labels restrict descriptive metadata only, never health eligibility.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        required = {label for label in (required_labels or []) if label}
        ranked = sorted(
            (
                recipe
                for recipe in self.recipes
                if recipe.eligible and required.issubset(recipe.labels)
            ),
            key=lambda recipe: (
                -recipe_relevance_score(recipe, query_terms, constraints, self._rules),
                recipe.recipe_id,
            ),
        )
        # Repeated input records cannot duplicate menu candidates.
        unique = list({recipe.recipe_id: recipe for recipe in reversed(ranked)}.values())
        unique.reverse()
        return unique if limit is None else unique[:limit]
