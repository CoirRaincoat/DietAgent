"""One retrieval contract with optional metadata and semantic ranking ports."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain.models import Constraints, Recipe
from app.schemas.recipe_meta import RecipeMeta


class RetrievalUnavailable(Exception):
    """An optional retrieval backend failed; constraints must not be relaxed."""


class RecipeRetriever(Protocol):
    def search(
        self, query_terms: list[str], constraints: Constraints, limit: int | None = None,
        *, required_labels: list[str] | None = None,
    ) -> list[Recipe]: ...


class MetadataStore(Protocol):
    def select(self, *, required_labels: Sequence[str]) -> Sequence[RecipeMeta]: ...


@dataclass(frozen=True)
class VectorHit:
    recipe_id: str
    score: float


class CandidateRanker(Protocol):
    def rank(
        self, query_terms: Sequence[str], *, candidate_ids: Sequence[str],
    ) -> Sequence[VectorHit]: ...
