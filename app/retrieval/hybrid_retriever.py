"""Optional metadata/ranking pipeline over canonical, rule-checked recipes.

No backend is created or contacted automatically. Empty candidates remain
empty. Backend failure is explicit. Vector payloads never become menu records.
"""

import math
from collections.abc import Iterable

from app.domain.models import Constraints, Recipe
from app.retrieval.core import CandidateRanker, MetadataStore, RetrievalUnavailable
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine


class HybridRetriever:
    def __init__(
        self, recipes: Iterable[Recipe], *, rules: RuleEngine | None = None,
        metadata: MetadataStore | None = None, ranker: CandidateRanker | None = None,
    ):
        self.recipes = {recipe.recipe_id: recipe for recipe in recipes}
        self.rules = rules or RuleEngine()
        self.lexical = KeywordRetriever(self.recipes.values())
        self.metadata = metadata
        self.ranker = ranker

    def search(
        self, query_terms: list[str], constraints: Constraints, limit: int | None = None,
        *, required_labels: list[str] | None = None,
    ) -> list[Recipe]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        if limit == 0:
            return []
        candidates = [
            recipe for recipe in self.lexical.search(
                query_terms, constraints, required_labels=required_labels,
            ) if self.rules.evaluate(recipe, constraints).allowed
        ]
        if not candidates:
            return []
        try:
            if self.metadata is not None:
                records = self.metadata.select(required_labels=required_labels or [])
                ids = {
                    row.recipe_id for row in records
                    if row.recipe_id in self.recipes
                    and row.fingerprint == self.recipes[row.recipe_id].fingerprint
                    and row.source_row == self.recipes[row.recipe_id].source_row
                }
                candidates = [recipe for recipe in candidates if recipe.recipe_id in ids]
            if not candidates:
                return []
            if self.ranker is not None:
                allowed_ids = {recipe.recipe_id for recipe in candidates}
                hits = self.ranker.rank(
                    query_terms, candidate_ids=tuple(recipe.recipe_id for recipe in candidates),
                )
                scores: dict[str, float] = {}
                for hit in hits:
                    if hit.recipe_id in allowed_ids and math.isfinite(hit.score):
                        scores[hit.recipe_id] = max(scores.get(hit.recipe_id, -math.inf), hit.score)
                # Preserve all safe records for limit=None; partial vector recall
                # does not prove the remaining catalog cannot form a valid meal.
                candidates.sort(key=lambda recipe: -scores.get(recipe.recipe_id, -math.inf))
        except Exception as error:
            raise RetrievalUnavailable("Optional recipe retrieval failed") from error
        # Re-check source objects after the optional backend, not its payloads.
        safe = [recipe for recipe in candidates if self.rules.evaluate(recipe, constraints).allowed]
        return safe if limit is None else safe[:limit]
