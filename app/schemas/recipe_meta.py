"""Traceable derived recipe metadata with no invented amounts or review status."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import Recipe
from app.normalization.normalizer import TermNormalizer


class IngredientTerm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    raw: str
    canonical: str | None


class RecipeMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["recipe-metadata-1"] = "recipe-metadata-1"
    recipe_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    source_row: int = Field(ge=1)
    title: str = Field(min_length=1)
    labels: list[str]
    eligible: bool
    quality_flags: list[str]
    ingredient_terms: list[IngredientTerm]
    unknown_ingredients: list[str]
    safety_review: Literal["not_reviewed"] = "not_reviewed"

    @classmethod
    def from_recipe(cls, recipe: Recipe, normalizer: TermNormalizer | None = None) -> "RecipeMeta":
        result = (normalizer or TermNormalizer()).normalize_terms(
            ingredient.name for ingredient in recipe.ingredients
        )
        return cls(
            recipe_id=recipe.recipe_id, fingerprint=recipe.fingerprint,
            source_row=recipe.source_row, title=recipe.name, labels=list(recipe.labels),
            eligible=recipe.eligible, quality_flags=list(recipe.quality_flags),
            ingredient_terms=[IngredientTerm(raw=e.raw, canonical=e.canonical) for e in result.evidence],
            unknown_ingredients=list(result.unknown_terms),
        )
