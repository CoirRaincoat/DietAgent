"""Prevent a name objective from buying loss of finite observation evidence.

Only for offline evaluation. Unrepresented foods are a limitation of the
selected finite metric, NOT missing source identity or unhealthy food. No new
ingredient, nutrient, safety alias or serving constraint is manufactured.
"""

from typing import Literal

from evaluation.declared_food_features import STAPLE_FEATURES
from evaluation.history_food_exposure import HistoricalFoodExposure

EvidenceLoss = Literal[
    "animal_identity",
    "protein_representation",
    "staple_representation",
    "declared_features",
    "history_coverage",
]


def name_evidence_loss(
    before: HistoricalFoodExposure,
    after: HistoricalFoodExposure,
    before_features: frozenset[str],
    after_features: frozenset[str],
    role: frozenset[str] = frozenset(),
) -> EvidenceLoss | None:
    """Reject less observable replacements, even with unknown-only history.

    History pair counts alone can hide a new current-record gap when past
    records are already unknown. Inspect current gaps as well. Allow newly
    represented evidence; it still cannot be scored as overall quality or
    compared by raw cosine sum to a different observation denominator.
    Nonempty finite features never prove complete or dominant ingredients.
    """
    if len(after.current_unspecified_animals) > len(before.current_unspecified_animals):
        return "animal_identity"
    if after.current_protein_feature_gap and not before.current_protein_feature_gap:
        return "protein_representation"
    if after.current_staple_feature_gap and not before.current_staple_feature_gap:
        return "staple_representation"
    if (
        role == {"staple"}
        and before_features & STAPLE_FEATURES
        and not after_features & STAPLE_FEATURES
    ):
        # A known pork/mushroom accompaniment cannot stand in for an
        # unrepresented staple body. Shared v2 includes whole glutinous rice;
        # neither policy establishes amounts or nutritional properties.
        return "staple_representation"
    if before_features and not after_features:
        return "declared_features"
    if (
        after.observed_pairs < before.observed_pairs
        or after.missing_pairs > before.missing_pairs
        or after.unspecified_animal_pairs > before.unspecified_animal_pairs
        or after.unrepresented_protein_pairs > before.unrepresented_protein_pairs
        or after.unrepresented_staple_pairs > before.unrepresented_staple_pairs
    ):
        return "history_coverage"
    return None
