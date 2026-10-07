"""Offline declared-food similarity across meals; never a quality/health score.

Inspired by feature-vector similarity in recommender diversity evaluation,
not an implementation copy or a dependency on another recommendation package.
Finite declared-food families supply binary features. Quantities, dominant
ingredients, consumption, safety and complete semantic similarity are unknown.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from math import sqrt
from typing import Literal

from app.domain.food_variety import FAMILY_VERSION, food_families
from app.domain.models import Recipe
from app.rules.engine import compact
from evaluation.declared_food_features import (
    OBSERVATION_FAMILY_VERSION,
    observation_food_families,
)

FeaturePolicy = Literal["planner_v4", "observation_v1"]


def _features(recipe: Recipe, policy: FeaturePolicy) -> frozenset[str]:
    if policy == "planner_v4":
        return food_families(recipe)
    if policy == "observation_v1":
        return observation_food_families(recipe)
    raise ValueError("Unknown content feature policy")


def _version(policy: FeaturePolicy) -> str:
    if policy == "planner_v4":
        return FAMILY_VERSION
    if policy == "observation_v1":
        return OBSERVATION_FAMILY_VERSION
    raise ValueError("Unknown content feature policy")


@dataclass(frozen=True)
class ContentObservation:
    """Separate same-name exposure from limited feature similarity/coverage."""

    meal_count: int
    dish_occurrences: int
    repeated_name_pairs: int
    declared_family_dish_occurrences: int
    missing_declared_family_occurrences: int
    same_role_cross_meal_pairs: int
    observed_family_pairs: int
    missing_family_pairs: int
    mean_declared_family_cosine: float | None
    family_policy_version: str = FAMILY_VERSION
    interpretation: str = (
        "有限已声明食材家族的同角色跨餐二元特征相似度，不是主料比例、"
        "营养/健康等价或质量分。缺特征不计零相似；菜名变多不证明食材变多。"
    )


@dataclass(frozen=True)
class AlignedContentComparison:
    """Compare the same observed slot-pairs; lost features cannot lower the mean."""

    before: ContentObservation
    after: ContentObservation
    aligned_same_role_pairs: int
    common_observed_family_pairs: int
    unobserved_in_either_pairs: int
    before_common_mean_cosine: float | None
    after_common_mean_cosine: float | None


def compare_content_similarity(
    before: Sequence[Sequence[Recipe]],
    after: Sequence[Sequence[Recipe]],
    *,
    feature_policy: FeaturePolicy = "planner_v4",
) -> AlignedContentComparison:
    """Align event/slot coordinates, refusing differently shaped trajectories.

    Only same-role pairs with features in BOTH versions enter the comparison.
    A coverage change is still reported by the individual observations; a
    common mean cannot establish quality or novelty for unobserved slots.
    """
    _version(feature_policy)
    if len(before) != len(after) or any(
        len(a) != len(b) for a, b in zip(before, after, strict=True)
    ):
        raise ValueError("Content comparison requires aligned meal and slot counts")
    possible = common = 0
    before_sum = after_sum = 0.0
    for first, second in combinations(range(len(before)), 2):
        for first_slot in range(len(before[first])):
            for second_slot in range(len(before[second])):
                a1, a2 = before[first][first_slot], before[second][second_slot]
                b1, b2 = after[first][first_slot], after[second][second_slot]
                role = frozenset(a1.categories)
                if not role or any(frozenset(r.categories) != role for r in (a2, b1, b2)):
                    continue
                possible += 1
                food_a1, food_a2 = _features(a1, feature_policy), _features(a2, feature_policy)
                food_b1, food_b2 = _features(b1, feature_policy), _features(b2, feature_policy)
                if not all((food_a1, food_a2, food_b1, food_b2)):
                    continue
                common += 1
                before_sum += len(food_a1 & food_a2) / sqrt(len(food_a1) * len(food_a2))
                after_sum += len(food_b1 & food_b2) / sqrt(len(food_b1) * len(food_b2))
    return AlignedContentComparison(
        before=observe_content_similarity(before, feature_policy=feature_policy),
        after=observe_content_similarity(after, feature_policy=feature_policy),
        aligned_same_role_pairs=possible,
        common_observed_family_pairs=common,
        unobserved_in_either_pairs=possible - common,
        before_common_mean_cosine=before_sum / common if common else None,
        after_common_mean_cosine=after_sum / common if common else None,
    )


def observe_content_similarity(
    menus: Sequence[Sequence[Recipe]], *, feature_policy: FeaturePolicy = "planner_v4"
) -> ContentObservation:
    """Compute cosine for observed same-role pairs from different meal events.

    No source edits are performed. Default features remain planner v4; the
    separate observation_v1 policy adds only finite offline declarations.
    A feature-policy change is NOT a menu improvement. A dish may share
    one family with another despite different amounts or preparation. Empty
    features and unknown roles yield no novelty credit. Repeated occurrences
    stay repeated, because these are recommended meals, not unique user/item
    interactions. A single meal has no cross-meal similarity observation.
    """
    version = _version(feature_policy)
    names = Counter(compact(r.name) for menu in menus for r in menu if compact(r.name))
    records = [
        [(frozenset(r.categories), _features(r, feature_policy)) for r in menu] for menu in menus
    ]
    total = sum(len(menu) for menu in menus)
    known = sum(bool(families) for menu in records for _, families in menu)
    possible = observed = 0
    similarity = 0.0
    for left, right in combinations(records, 2):
        for role_a, food_a in left:
            for role_b, food_b in right:
                if not role_a or role_a != role_b:
                    continue
                possible += 1
                if not food_a or not food_b:
                    continue
                observed += 1
                similarity += len(food_a & food_b) / sqrt(len(food_a) * len(food_b))
    return ContentObservation(
        meal_count=len(menus),
        dish_occurrences=total,
        repeated_name_pairs=sum(n * (n - 1) // 2 for n in names.values()),
        declared_family_dish_occurrences=known,
        missing_declared_family_occurrences=total - known,
        same_role_cross_meal_pairs=possible,
        observed_family_pairs=observed,
        missing_family_pairs=possible - observed,
        mean_declared_family_cosine=similarity / observed if observed else None,
        family_policy_version=version,
    )
