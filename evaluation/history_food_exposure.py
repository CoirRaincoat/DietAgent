"""Finite offline declared-content exposure, not consumed food or quality."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt

from app.domain.meal_history import HISTORY_LIMIT
from app.domain.models import Recipe
from app.rules.engine import compact
from evaluation.animal_source_uncertainty import unspecified_animal_declarations
from evaluation.declared_food_features import (
    PROTEIN_FEATURES,
    STAPLE_FEATURES,
    ObservationPolicy,
    observation_food_families,
    observation_version,
)

COSINE_UNIT = 1_000_000
EXPOSURE_VERSION = "declared-history-v3-animal-and-role-evidence"
SHARED_EXPOSURE_VERSION = "declared-history-v4-shared-food-and-staple-evidence"
_PROTEIN_FEATURES = frozenset({"chicken", "pork", "shrimp", "yellow_croaker", "egg", "tofu"})


@dataclass(frozen=True)
class HistoricalFoodExposure:
    """Unknown pairs are missing, never counted as zero similarity."""

    observed_pairs: int
    missing_pairs: int
    weighted_cosine_units: int | None
    unspecified_animal_pairs: int = 0
    current_unspecified_animals: tuple[str, ...] = ()
    evidence_policy_version: str = EXPOSURE_VERSION
    unrepresented_protein_pairs: int = 0
    current_protein_feature_gap: bool = False
    unrepresented_staple_pairs: int = 0
    current_staple_feature_gap: bool = False


def _protein_feature_gap(
    recipe: Recipe, features: frozenset[str], feature_policy: ObservationPolicy
) -> bool:
    # A recognized carrot cannot supply an unrepresented protein source.
    # Legacy v1 omitted explicit beef/duck; opt-in shared v2 observes them.
    # Even presence of these finite features does not establish dominance,
    # servings or protein grams; this is a necessary, not sufficient guard.
    represented = PROTEIN_FEATURES if feature_policy == "shared_v2" else _PROTEIN_FEATURES
    return frozenset(recipe.categories) == {"protein"} and not features & represented


def _staple_feature_gap(
    recipe: Recipe, features: frozenset[str], feature_policy: ObservationPolicy
) -> bool:
    return (
        feature_policy == "shared_v2"
        and frozenset(recipe.categories) == {"staple"}
        and not features & STAPLE_FEATURES
    )


def history_food_exposure(
    recipe: Recipe,
    history: Sequence[Sequence[Recipe]],
    *,
    protect_animal_identity: bool = True,
    feature_policy: ObservationPolicy = "observation_v1",
) -> HistoricalFoodExposure:
    """Compare exact same roles in the last eight recommended meals.

    Each past dish name counts once per meal, whose recency weight is 1..8.
    Cosine is rounded per pair to one-millionth units for deterministic bounded
    search. This is an explicit engineering objective, not nutritional or
    dominant-ingredient similarity. Missing coverage must be held constant
    when using its sum to compare candidate substitutions.
    Unspecified 肉末/里脊肉 etc makes that pair insufficient to establish
    dish-level content novelty, even if accompanying plants are recognized.
    An exclusive protein-role dish also needs a represented protein-source
    declaration; known vegetable accompaniments alone cannot supply it.
    False is a labeled legacy diagnostic for reproducing the rejected metric,
    not the ranking policy. Raw family-presence observations remain separate.
    Opt-in shared_v2 reuses explicit protein declarations and whole grains;
    staple roles require a represented staple feature even with known garnish.
    Changing vocabulary/coverage is not a quality or novelty gain.
    """
    observation_version(feature_policy)
    role = frozenset(recipe.categories)
    foods = observation_food_families(recipe, feature_policy=feature_policy)
    unspecified = unspecified_animal_declarations(recipe)
    role_gap = _protein_feature_gap(recipe, foods, feature_policy)
    staple_gap = _staple_feature_gap(recipe, foods, feature_policy)
    observed = missing = value = animal_pairs = role_pairs = staple_pairs = 0
    for weight, menu in enumerate(history[-HISTORY_LIMIT:], 1):
        seen: set[str] = set()
        for past in menu:
            name = compact(past.name)
            if not name or name in seen:
                continue
            seen.add(name)
            if not role or frozenset(past.categories) != role:
                continue
            previous = observation_food_families(past, feature_policy=feature_policy)
            if protect_animal_identity and (unspecified or unspecified_animal_declarations(past)):
                missing += 1
                animal_pairs += 1
                continue
            if protect_animal_identity and (
                role_gap or _protein_feature_gap(past, previous, feature_policy)
            ):
                missing += 1
                role_pairs += 1
                continue
            if protect_animal_identity and (
                staple_gap or _staple_feature_gap(past, previous, feature_policy)
            ):
                missing += 1
                staple_pairs += 1
                continue
            if not foods or not previous:
                missing += 1
                continue
            observed += 1
            value += weight * round(
                COSINE_UNIT * len(foods & previous) / sqrt(len(foods) * len(previous))
            )
    return HistoricalFoodExposure(
        observed,
        missing,
        value if observed else None,
        animal_pairs,
        tuple(sorted(unspecified)),
        (
            (SHARED_EXPOSURE_VERSION if feature_policy == "shared_v2" else EXPOSURE_VERSION)
            if protect_animal_identity
            else (
                "declared-history-v1-legacy-unprotected"
                if feature_policy == "observation_v1"
                else "unprotected/" + observation_version(feature_policy)
            )
        ),
        role_pairs,
        role_gap,
        staple_pairs,
        staple_gap,
    )
