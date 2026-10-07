"""Lexical retrieval with explicit label filtering and catalog fallback."""

from collections.abc import Iterable, Sequence

from app.domain.context_exclusions import context_exclusion_hits
from app.domain.dining_scenes import supported_scene_preferences
from app.domain.matching_tags import (
    flavor_strength,
    scene_reference_mask,
    supported_flavor_preferences,
)
from app.domain.meal_context import meal_fit
from app.domain.menu_group_preferences import is_menu_group_preference
from app.domain.method_preferences import method_reference_mask
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
        if is_menu_group_preference(term):
            value += 3 if rules.preference_matches(recipe, term) else 0
            continue
        synonyms = rules.aliases_for(term)
        if any(contains_term(recipe.name, alias) for alias in synonyms):
            value += 4
        if rules.food_matches(recipe, term):
            value += 3
        if any(contains_term(label, term) for label in recipe.labels):
            value += 1
    # Resolve the accumulated list together: an old 酸 cannot earn ranking
    # credit after the same conversation explicitly records 不要酸.
    flavors = supported_flavor_preferences(constraints.preferences)
    value += 0.5 * sum(flavor_strength(recipe, flavor) > 0 for flavor in flavors)
    value += 0.5 * method_reference_mask(recipe, constraints.preferences).bit_count()
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    if scenes:
        value += 0.5 * scene_reference_mask(recipe, scenes).bit_count()
    # Exact source tags outrank assistant references; stale cached tags do not
    # receive source credit. These are soft ordering cues, never exclusions.
    fit = meal_fit(recipe, constraints.meal_type)
    value += 0.5 if fit == "matched" else 0.25 if fit == "suggested" else 0.0
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
        required_scenes: list[str] | None = None,
    ) -> list[Recipe]:
        """Rank by food, title and label matches without inventing recipes.

        No lexical hit is not a proof of infeasibility. Without an explicit
        label filter, limit=None preserves all eligible records for planning.
        required_labels requires all exact labels; no match remains empty.
        required_scenes requires ALL source/title or separately source-bound
        assistant scene references, not certified suitability. Unknown references
        cannot pass this opt-in filter. The ordinary planner keeps unknown scenes
        as candidates and discloses them instead of declaring infeasibility.
        Explicit negative scene/meal source references are screened before ranking;
        unknown and assistant-only compatibility remain candidates with disclosure.
        Labels restrict descriptive metadata only, never health eligibility.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        required = {label for label in (required_labels or []) if label}
        scene_refs = tuple(dict.fromkeys(tag for tag in (required_scenes or []) if tag))
        ranked = sorted(
            (
                recipe
                for recipe in self.recipes
                if recipe.eligible
                and not context_exclusion_hits(recipe, constraints)
                and required.issubset(recipe.labels)
                and (
                    not scene_refs
                    or scene_reference_mask(recipe, scene_refs) == (1 << len(scene_refs)) - 1
                )
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
