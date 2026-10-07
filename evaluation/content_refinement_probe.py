"""Offline two-stage refinement; protect name-first baseline, not just seed."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine, compact
from evaluation.declared_food_features import ObservationPolicy
from evaluation.history_saturation_probe import HistoryProbe, probe_history_rotation


@dataclass
class ContentRefinement:
    baseline: HistoryProbe
    refinement: HistoryProbe | None
    recipes: list[Recipe]
    evaluated: int
    status: str


def probe_content_refinement(
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
    pool_strategy: Literal["flat", "declared_food_stratified"] = "flat",
    preserve_name_evidence: bool = True,
    feature_policy: ObservationPolicy = "observation_v1",
) -> ContentRefinement:
    """Name rotation, then guarded content improvement within one total budget.

    Each content exchange must preserve the baseline's name exposure and all
    existing strict scene/goal/method/food/role/scope gates. Unknown pairs stay
    unknown, and baseline feasibility is not relaxed. Two static candidate
    feature scans add cost; search counts share a global cap rather than
    renewing it. Finite pools and budget outcomes do not prove optimality.
    This remains offline and has no independent quality certification.
    """
    if [[compact(r.name) for r in m] for m in history_menus] != [
        [compact(name) for name in m] for m in history
    ]:
        raise ValueError("Content refinement requires matching historical recipe records")
    baseline = probe_history_rotation(
        menu,
        candidates,
        constraints,
        rules,
        history,
        pool_limit=pool_limit,
        evaluation_limit=evaluation_limit,
        replace_slot=replace_slot,
        query_terms=query_terms,
        recheck_soft_preferences=recheck_soft_preferences,
        pool_strategy=pool_strategy,
        preserve_name_evidence=preserve_name_evidence,
        history_menus=history_menus,
        feature_policy=feature_policy,
    )
    remaining = evaluation_limit - baseline.evaluated
    if remaining <= 0:
        return ContentRefinement(
            baseline,
            None,
            baseline.recipes,
            baseline.evaluated,
            "baseline_used_shared_budget_NOT_optimal",
        )
    refinement = probe_history_rotation(
        baseline.recipes,
        candidates,
        constraints,
        rules,
        history,
        ranking="declared_content",
        history_menus=history_menus,
        pool_limit=pool_limit,
        evaluation_limit=remaining,
        replace_slot=replace_slot,
        query_terms=query_terms,
        recheck_soft_preferences=recheck_soft_preferences,
        pool_strategy=pool_strategy,
        preserve_name_evidence=preserve_name_evidence,
        feature_policy=feature_policy,
    )
    return ContentRefinement(
        baseline,
        refinement,
        refinement.recipes,
        baseline.evaluated + refinement.evaluated,
        "refinement/" + refinement.status,
    )
