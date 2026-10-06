"""Offline qualitative reference types, not nutrient targets or health effects.

Duplicate positive references cannot prove additional meal adequacy. This
experimental representation retains the *types* already observed for each
configured goal, separately from caution frequencies. It deliberately does
not claim equivalence to the serving planner's additive per-dish scores.
"""

from app.domain.health_evidence import HealthRule
from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine, contains_term


def positive_reference_labels(
    recipe: Recipe, constraints: Constraints, rules: RuleEngine
) -> frozenset[str]:
    """Return source-supported positive types, including their goal identity.

    Prefer-term identities are kept separately: a retained 豆腐 reference may
    not replace the only 燕麦 reference merely because both earned two points.
    Category-food references remain an explicitly coarse configured category
    signal, not portions, vegetable quantity or title-derived health claims.
    Unknown goals/disabled role bonuses cannot create positive observations.
    """
    labels: set[str] = set()
    for goal in dict.fromkeys(constraints.health_goals):
        evidence = rules.goal_evidence(recipe, goal)
        configured = rules.config["health_goals"].get(goal)
        if evidence is None or configured is None or not evidence.positive_rank_enabled:
            continue
        rule = HealthRule.from_mapping(configured)
        if evidence.category_rank_enabled and evidence.category_foods:
            labels.add(f"{goal}:category_reference")
        for term in rule.prefer_terms:
            if any(contains_term(food, term) for food in evidence.preferred_foods):
                labels.add(f"{goal}:preferred_term:{term}")
        labels.update(f"{goal}:method:{method}" for method in evidence.good_methods)
    return frozenset(labels)
