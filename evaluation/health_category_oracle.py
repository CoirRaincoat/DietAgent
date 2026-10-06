"""Separate authored role/goal-category reviews, not medical or quality scores."""

from collections.abc import Mapping
from typing import Any

ORACLE_VERSION = "authored-health-category-scope-v1"
_ROLES = {"vegetable", "protein", "staple", "soup"}


def health_category_ranking_findings(
    result: Mapping[str, Any],
    expected: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Check isolated category contributions against separate source role reviews.

    Args:
        result: Source-ID-bearing menu and optional replacement suggestions.
        expected: Nonempty unique ``goals``, ``reviewed_roles`` (one culinary
            role per ID), ``reviewed_rank_categories`` and
            ``reviewed_positive_roles`` (role lists per goal; empty positive
            roles means no extra positive-rank restriction).
            These tables are separately authored, not classifier outputs.
        observed: Source ID to goal to actual evidence trace, including
            ``category_foods``, ``positive_rank_enabled`` and the isolated
            integer ``category_bonus_NOT_health``. Not response matched bits.

    Returns:
        Missing/malformed observations or cross-role category contributions.
        Passing only proves this finite engineering-role scoping. It does not
        validate the underlying food parser, healthy doses, ingredient amounts,
        commercial composition, clinical suitability or overall menu quality.
        Identity, food/source facts, safety and net benefit need separate gates.
    """
    goals = expected.get("goals") if isinstance(expected, Mapping) else None
    roles = expected.get("reviewed_roles") if isinstance(expected, Mapping) else None
    categories = expected.get("reviewed_rank_categories") if isinstance(expected, Mapping) else None
    positive_roles = (
        expected.get("reviewed_positive_roles") if isinstance(expected, Mapping) else None
    )
    if (
        not isinstance(goals, list)
        or not goals
        or any(not isinstance(g, str) or not g.strip() for g in goals)
        or len(goals) != len(set(goals))
        or not isinstance(roles, Mapping)
        or not roles
        or any(
            not isinstance(key, str) or not key or not isinstance(role, str) or role not in _ROLES
            for key, role in roles.items()
        )
        or not isinstance(categories, Mapping)
        or any(
            not isinstance(categories.get(g), list)
            or any(not isinstance(r, str) or r not in _ROLES for r in categories[g])
            or len(categories[g]) != len(set(categories[g]))
            for g in goals
        )
        or not isinstance(positive_roles, Mapping)
        or any(
            not isinstance(positive_roles.get(g), list)
            or any(not isinstance(r, str) or r not in _ROLES for r in positive_roles[g])
            or len(positive_roles[g]) != len(set(positive_roles[g]))
            for g in goals
        )
    ):
        return [{"type": "category_review_missing_or_invalid"}]
    if not isinstance(result, Mapping):
        return [{"type": "menu_missing_or_invalid"}]
    menu, suggestions = result.get("menu"), result.get("replacement_suggestions", [])
    if (
        not isinstance(menu, list)
        or not menu
        or not isinstance(suggestions, list)
        or any(not isinstance(r, Mapping) for r in [*menu, *suggestions])
    ):
        return [{"type": "menu_missing_or_invalid"}]
    if not isinstance(observed, Mapping):
        return [{"type": "category_source_observations_missing"}]
    findings: list[dict[str, Any]] = []
    for output, records in (("menu", menu), ("suggestions", suggestions)):
        for record in records:
            key = record.get("recipe_id")
            if not isinstance(key, str) or not key or key not in roles:
                findings.append(
                    {"type": "source_role_review_missing", "recipe_id": key, "output": output}
                )
                continue
            source = observed.get(key)
            for goal in goals:
                trace = source.get(goal) if isinstance(source, Mapping) else None
                foods = trace.get("category_foods") if isinstance(trace, Mapping) else None
                enabled = trace.get("positive_rank_enabled") if isinstance(trace, Mapping) else None
                bonus = (
                    trace.get("category_bonus_NOT_health") if isinstance(trace, Mapping) else None
                )
                if (
                    not isinstance(foods, list)
                    or any(not isinstance(f, str) or not f for f in foods)
                    or not isinstance(enabled, bool)
                    or type(bonus) is not int
                    or bonus not in (0, 2)
                ):
                    findings.append(
                        {
                            "type": "category_trace_missing_or_invalid",
                            "recipe_id": key,
                            "goal": goal,
                            "output": output,
                        }
                    )
                    continue
                supported_positive = not positive_roles[goal] or roles[key] in positive_roles[goal]
                if enabled != supported_positive:
                    findings.append(
                        {
                            "type": "positive_role_scope_mismatch",
                            "recipe_id": key,
                            "goal": goal,
                            "output": output,
                        }
                    )
                wanted = 2 if foods and supported_positive and roles[key] in categories[goal] else 0
                if bonus != wanted:
                    findings.append(
                        {
                            "type": "category_scope_mismatch",
                            "recipe_id": key,
                            "goal": goal,
                            "output": output,
                            "reviewed_role": roles[key],
                            "observed_bonus_NOT_health": bonus,
                            "expected_category_contribution": wanted,
                        }
                    )
    return findings
