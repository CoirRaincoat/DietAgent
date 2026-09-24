"""Recipe retrieval tool; never manufactures catalog records."""

from app.domain.models import Constraints, Recipe
from app.retrieval.keyword import KeywordRetriever


class RecipeSearchTool:
    def __init__(self, retriever: KeywordRetriever):
        self.retriever = retriever

    def __call__(
        self, query_terms: list[str], constraints: Constraints,
        limit: int | None = None, required_labels: list[str] | None = None,
    ) -> list[Recipe]:
        return self.retriever.search(
            query_terms, constraints, limit, required_labels=required_labels,
        )
