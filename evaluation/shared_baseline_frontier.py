"""Offline extension AFTER a complete protected flat baseline, never instead.

Share one evaluation cap. Keep the actual flat result as incumbent; a sampling
hint or an old slot alone is not a no-regression guarantee. No serving import.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine
from evaluation.content_refinement_probe import ContentRefinement, probe_content_refinement
from evaluation.declared_food_features import ObservationPolicy
from evaluation.history_saturation_probe import HistoryProbe, probe_history_rotation


@dataclass
class SharedBaselineFrontier:
    baseline: ContentRefinement
    extension: HistoryProbe | None
    recipes: list[Recipe]
    evaluated: int
    status: str


def probe_shared_baseline_frontier(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    history: Sequence[Sequence[str]],
    history_menus: Sequence[Sequence[Recipe]],
    *,
    pool_limit: int = 32,
    evaluation_limit: int = 50000,
    replace_slot: int | None = None,
    query_terms: Sequence[str] = (),
    recheck_soft_preferences: bool = True,
    feature_policy: ObservationPolicy = "observation_v1",
) -> SharedBaselineFrontier:
    """Spend only unused baseline budget on stratified content exchanges.

    A complete protected two-stage flat baseline runs first with the same
    full cap as standalone flat evaluation. Preserve its result, not a newly
    computed lower-budget substitute. Extension uses all original gates and
    a strictly improving observed-content/name objective; unknown-only slots
    stay fixed. Baseline can consume the whole cap, leaving no extension.
    Count cap is not a wall-clock or TTFT guarantee; feature scans cost time.
    This ensures finite proxy non-regression, NOT actual menu quality.
    """
    baseline = probe_content_refinement(
        menu,
        candidates,
        constraints,
        rules,
        history,
        history_menus,
        pool_limit=pool_limit,
        evaluation_limit=evaluation_limit,
        replace_slot=replace_slot,
        query_terms=query_terms,
        recheck_soft_preferences=recheck_soft_preferences,
        pool_strategy="flat",
        preserve_name_evidence=True,
        feature_policy=feature_policy,
    )
    remaining = evaluation_limit - baseline.evaluated
    if remaining <= 0:
        return SharedBaselineFrontier(
            baseline,
            None,
            baseline.recipes,
            baseline.evaluated,
            "baseline_used_shared_budget_NOT_optimal",
        )
    extension = probe_history_rotation(
        baseline.recipes,
        candidates,
        constraints,
        rules,
        history,
        history_menus=history_menus,
        ranking="declared_content",
        pool_strategy="declared_food_stratified",
        preserve_name_evidence=True,
        feature_policy=feature_policy,
        pool_limit=pool_limit,
        evaluation_limit=remaining,
        replace_slot=replace_slot,
        query_terms=query_terms,
        recheck_soft_preferences=recheck_soft_preferences,
    )
    return SharedBaselineFrontier(
        baseline,
        extension,
        extension.recipes,
        baseline.evaluated + extension.evaluated,
        "shared_baseline/" + extension.status,
    )
