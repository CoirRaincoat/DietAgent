"""Independently authored source audience/meal reviews, not production verdicts."""

from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-source-meal-context-v1"
_MEALS = {"早餐", "午餐", "晚餐", "下午茶", "夜宵"}


def context_findings(
    menu: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    suggestions: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Use reviewer-owned IDs/audience/meals; positive returned tags cannot pass.

    Exact source authenticity remains a separate gate. A strict tag expectation
    fails both absent and other-meal evidence. Non-strict use checks ordinary
    audience only, and explicitly must not be read as verified meal suitability.
    No clinical suitability, serving size or full-catalog accuracy is asserted.
    """
    reviews = expected.get("reviewed_context")
    meal = expected.get("meal_type")
    strict = expected.get("require_matching_tags", True)
    if (
        not isinstance(meal, str)
        or meal not in _MEALS
        or not isinstance(strict, bool)
        or not isinstance(reviews, Mapping)
        or not reviews
    ):
        return [{"type": "context_review_missing_or_invalid"}]
    for key, review in reviews.items():
        if (
            not isinstance(key, str)
            or not isinstance(review, Mapping)
            or review.get("audience") not in ("ordinary", "infant_only")
            or not isinstance(review.get("meals"), list)
            or any(not isinstance(tag, str) or tag not in _MEALS for tag in review["meals"])
        ):
            return [{"type": "context_review_missing_or_invalid"}]
    findings: list[dict[str, Any]] = []
    if not menu:
        findings.append({"type": "menu_missing"})
    for output, records in (("menu", menu), ("suggestions", suggestions)):
        for record in records:
            key = str(record.get("recipe_id", ""))
            review = reviews.get(key)
            finding: str | None = None
            if review is None:
                finding = "source_review_missing"
            elif review["audience"] == "infant_only":
                finding = "infant_source_in_ordinary_menu"
            elif strict and meal not in review["meals"]:
                finding = "meal_evidence_missing" if not review["meals"] else "other_meal_evidence"
            if finding:
                findings.append({"output": output, "recipe_id": key, "type": finding})
    return findings
