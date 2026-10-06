"""Load assistant-authored review sidecars without replacing recipe source data.

Every selected registry record must bind to the exact normalized source before
an exporter creates a review directory. Missing or stale records fail closed;
they are not silently dropped, promoted into serving, or called human labels.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.models import Recipe
from app.domain.preparation_enrichment import AssistantCuration, audit_preparation

CURATION_VERSION = "assistant-preparation-curation-v1"


class PreparationCurationRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["assistant-preparation-curation-v1"]
    records: tuple[AssistantCuration, ...]

    @model_validator(mode="after")
    def unique_recipe_ids(self) -> PreparationCurationRegistry:
        identifiers = [record.recipe_id for record in self.records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate recipe_id in assistant preparation registry")
        return self


def load_preparation_curations(
    path: Path,
    recipes: Mapping[str, Recipe],
    *,
    expected_sha256: str | None = None,
) -> dict[str, AssistantCuration]:
    """Validate the entire registry and every source binding, or return nothing."""
    registry_bytes = path.read_bytes()
    if (
        expected_sha256 is not None
        and hashlib.sha256(registry_bytes).hexdigest() != expected_sha256
    ):
        raise ValueError("Assistant preparation registry changed before validation")
    registry = PreparationCurationRegistry.model_validate_json(registry_bytes.decode("utf-8-sig"))
    selected: dict[str, AssistantCuration] = {}
    for record in registry.records:
        recipe = recipes.get(record.recipe_id)
        if recipe is None:
            raise ValueError(f"Curation source recipe is missing: {record.recipe_id}")
        # Domain validation owns identity/declaration rules; do not duplicate or
        # weaken them here by matching names or treating SHA mismatch as a skip.
        audit_preparation(recipe, curation=record)
        selected[record.recipe_id] = record
    return selected
