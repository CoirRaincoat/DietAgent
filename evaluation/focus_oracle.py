"""Compare declared culinary focus with separate source reviews, not scores."""

from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-culinary-focus-v1"


def culinary_focus_findings(
    result: Mapping[str, Any],
    expected: Mapping[str, Any],
    observed: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    """Check source focus observations without importing the production mapper.

    Args:
        result: Source-ID-bearing menu and optional replacement suggestions.
        expected: Independently authored ``reviewed_focus`` maps every output
            ID to unique family strings. Empty lists mean evidence is not
            established for ranking, not absence of foods or good diversity.
        observed: Culinary focus computed from the source being evaluated,
            never response success, grades or the authored table itself.

    Returns:
        Missing/malformed or mismatching review/observation findings. Passing
        establishes consistency only for this finite review, not source identity,
        eligibility, nutritional dominance or overall recommendation quality.
        Source traceability and every hard gate require separate checks.
    """
    reviews = expected.get("reviewed_focus") if isinstance(expected, Mapping) else None
    if not isinstance(reviews, Mapping) or not reviews:
        return [{"type": "focus_review_missing_or_invalid"}]
    for key, families in reviews.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(families, list)
            or any(not isinstance(family, str) or not family.strip() for family in families)
            or len(set(families)) != len(families)
        ):
            return [{"type": "focus_review_missing_or_invalid"}]
    menu = result.get("menu")
    suggestions = result.get("replacement_suggestions", [])
    if (
        not isinstance(menu, list)
        or not menu
        or not isinstance(suggestions, list)
        or any(not isinstance(r, Mapping) for r in [*menu, *suggestions])
    ):
        return [{"type": "menu_missing_or_invalid"}]
    if observed is None:
        return [{"type": "source_focus_observations_missing"}]
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
            actual = observed.get(key)
            if actual is None:
                findings.append(
                    {"output": output, "recipe_id": key, "type": "source_focus_unavailable"}
                )
            elif (
                not isinstance(actual, Sequence)
                or isinstance(actual, str)
                or any(not isinstance(family, str) or not family.strip() for family in actual)
                or sorted(actual) != sorted(wanted)
            ):
                findings.append(
                    {
                        "output": output,
                        "recipe_id": key,
                        "type": "culinary_focus_mismatch",
                        "reviewed_focus": wanted,
                        "observed_focus": actual,
                    }
                )
    return findings
