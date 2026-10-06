"""Authored primary-role reviews; no production classifier or health score."""

from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-primary-roles-v1"
_ROLES = frozenset({"protein", "vegetable", "staple", "soup"})


def primary_role_findings(
    result: Mapping[str, Any],
    expected: Mapping[str, Any],
    observed_roles: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    """Compare source-catalog roles against separately authored review roles.

    Args:
        result: Response menu and replacement suggestions, each with source IDs.
        expected: ``reviewed_roles`` maps every emitted ID to one reviewed
            ordinary-meal role. Never generate reviews from the classifier.
        observed_roles: Roles in the source catalog being tested, not response
            success bits, badges, health matches or the review itself.

    Returns:
        Explicit failures for missing/malformed reviews, outputs, observations
        or mismatched roles. Empty means only this finite role review passed.
        Source identity, hard constraints and recipe suitability require their
        own gates. No quantities or universal classification accuracy are proven.
    """
    if not isinstance(expected, Mapping):
        return [{"type": "role_review_missing_or_invalid"}]
    reviews = expected.get("reviewed_roles")
    if not isinstance(reviews, Mapping) or not reviews:
        return [{"type": "role_review_missing_or_invalid"}]
    if any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(role, str)
        or role not in _ROLES
        for key, role in reviews.items()
    ):
        return [{"type": "role_review_missing_or_invalid"}]
    menu = result.get("menu")
    suggestions = result.get("replacement_suggestions", [])
    if (
        not isinstance(menu, list)
        or not menu
        or not isinstance(suggestions, list)
        or any(not isinstance(record, Mapping) for record in [*menu, *suggestions])
    ):
        return [{"type": "menu_missing_or_invalid"}]
    if observed_roles is None:
        return [{"type": "source_role_observations_missing"}]
    findings: list[dict[str, Any]] = []
    for output, records in (("menu", menu), ("suggestions", suggestions)):
        for record in records:
            key = record.get("recipe_id")
            if not isinstance(key, str) or not key:
                findings.append({"output": output, "type": "recipe_id_missing_or_invalid"})
                continue
            wanted = reviews.get(key)
            if wanted is None:
                findings.append(
                    {"output": output, "recipe_id": key, "type": "source_review_missing"}
                )
                continue
            observed = observed_roles.get(key)
            if observed is None:
                findings.append(
                    {"output": output, "recipe_id": key, "type": "source_role_unavailable"}
                )
            elif isinstance(observed, str) or list(observed) != [wanted]:
                findings.append(
                    {
                        "output": output,
                        "recipe_id": key,
                        "type": "primary_role_mismatch",
                        "reviewed_role": wanted,
                        "observed_roles": observed,
                    }
                )
    return findings
