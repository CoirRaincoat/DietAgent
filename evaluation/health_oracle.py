"""Reviewer-authored caution requirements, independent of production scoring."""

from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-health-reporting-v1"


def health_reporting_findings(
    result: Mapping[str, Any], expected: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Check source-reviewed cautions rather than accepting a returned success bit.

    Review records are manually authored from source ingredients/preparation;
    they are not generated from RuleEngine, nutrition output or its score.
    Every emitted recipe and requested goal needs a review. Source authenticity
    is a separate gate. Empty caution reviews are not a health certification.
    No clinical suitability, quantitative nutrients or human rating is inferred.
    """
    goals = expected.get("goals")
    reviews = expected.get("reviewed_goals")
    if (
        not isinstance(goals, list)
        or not goals
        or any(not isinstance(goal, str) or not goal for goal in goals)
        or len(set(goals)) != len(goals)
        or not isinstance(reviews, Mapping)
        or not reviews
    ):
        return [{"type": "health_review_missing_or_invalid"}]
    for key, reviewed in reviews.items():
        if not isinstance(key, str) or not isinstance(reviewed, Mapping):
            return [{"type": "health_review_missing_or_invalid"}]
        for goal in goals:
            value = reviewed.get(goal)
            if (
                not isinstance(value, Mapping)
                or not isinstance(value.get("cautions"), list)
                or any(not isinstance(token, str) or not token for token in value["cautions"])
                or not isinstance(value.get("configured"), bool)
            ):
                return [{"type": "health_review_missing_or_invalid"}]
    menu = result.get("menu", [])
    suggestions = result.get("replacement_suggestions", [])
    if not isinstance(menu, list) or not menu or not isinstance(suggestions, list):
        return [{"type": "menu_missing_or_invalid"}]
    findings: list[dict[str, Any]] = []

    def check_matches(matches: object, records: Sequence[Mapping[str, Any]], output: str) -> None:
        if not isinstance(matches, list):
            findings.append({"output": output, "type": "goal_reporting_missing"})
            return
        by_goal = {item.get("goal"): item for item in matches if isinstance(item, Mapping)}
        for goal in goals:
            match = by_goal.get(goal)
            if match is None:
                findings.append({"output": output, "goal": goal, "type": "goal_reporting_missing"})
                continue
            if match.get("status") not in {"caution", "preference_match", "insufficient_data"}:
                findings.append(
                    {"output": output, "goal": goal, "type": "unsupported_health_status"}
                )
            if not isinstance(match.get("limitation"), str) or not match["limitation"].strip():
                findings.append({"output": output, "goal": goal, "type": "health_scope_missing"})
            text = str(match.get("ingredient_names", [])) + str(match.get("reasons", []))
            for record in records:
                key = record.get("recipe_id")
                reviewed = reviews.get(key)
                if reviewed is None:
                    findings.append(
                        {"output": output, "recipe_id": key, "type": "source_review_missing"}
                    )
                    continue
                value = reviewed[goal]
                if not value["configured"] and match.get("status") != "insufficient_data":
                    findings.append(
                        {"output": output, "goal": goal, "type": "unconfigured_goal_claim"}
                    )
                for token in value["cautions"]:
                    if token not in text or match.get("status") != "caution":
                        findings.append(
                            {
                                "output": output,
                                "recipe_id": key,
                                "goal": goal,
                                "token": token,
                                "type": "source_caution_not_reported",
                            }
                        )

    if any(not isinstance(record, Mapping) for record in [*menu, *suggestions]):
        return [{"type": "menu_missing_or_invalid"}]
    nutrition = result.get("nutrition_analysis")
    check_matches(
        nutrition.get("goal_matches") if isinstance(nutrition, Mapping) else None, menu, "menu"
    )
    for record in suggestions:
        analysis = record.get("nutrition")
        check_matches(
            analysis.get("goal_matches") if isinstance(analysis, Mapping) else None,
            [record],
            "suggestions",
        )
    # This detects an explicit attainment claim, not every possible misleading
    # sentence. Clinical/human review remains outside this finite validator.
    reason = str(result.get("reason", ""))
    for claim in ("本餐健康目标已达标", "保证降低血压", "保证增肌效果", "符合个人低钠目标"):
        if claim in reason:
            findings.append({"type": "unsupported_health_attainment_claim", "claim": claim})
    return findings
