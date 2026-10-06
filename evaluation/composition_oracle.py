"""Exact quantities against an authored source-review index, never service labels."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-source-composition-oracle-v1"


def composition_findings(
    menu: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    suggestions: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Check menu and slot-one proposals using a reviewer-owned recipe-ID index.

    Args:
        menu: Output dishes with source IDs; returned labels are ignored.
        expected: Exact meat/no-meat quotas and reviewed_recipe_kinds mapping.
            The mapping must be authored against the same source snapshot, not
            generated from production classifiers or the model's verdicts.
        suggestions: Each suggestion replaces only the first dish.

    Returns:
        Finite quantity mismatches or missing review evidence. Unreviewed IDs
        fail this check, never become vegetarian because no meat was found.
        Source authenticity is checked separately by catalog_traceability.
    """
    index = expected.get("reviewed_recipe_kinds", {})
    valid = {"meat", "vegetarian", "soup", "other"}
    if (
        not isinstance(index, Mapping)
        or not index
        or any(
            not isinstance(key, str) or not isinstance(kind, str) or kind not in valid
            for key, kind in index.items()
        )
    ):
        return [{"type": "review_index_missing_or_invalid"}]
    findings: list[dict[str, Any]] = []
    menus = [("menu", list(menu))]
    if suggestions and not menu:
        findings.append({"type": "suggestion_without_menu"})
    if menu:
        menus.extend(
            (f"suggestion_{i}", [suggestion, *menu[1:]])
            for i, suggestion in enumerate(suggestions, 1)
        )
    for name, dishes in menus:
        unknown = [
            str(item.get("recipe_id", "")) for item in dishes if item.get("recipe_id") not in index
        ]
        if unknown:
            findings.append(
                {"output": name, "type": "source_review_missing", "recipe_ids": unknown}
            )
            continue
        counts = Counter(index[item["recipe_id"]] for item in dishes)
        for kind in ("meat", "vegetarian"):
            wanted = expected.get(kind)
            if wanted is not None and counts[kind] != wanted:
                findings.append(
                    {
                        "output": name,
                        "type": "quantity_mismatch",
                        "kind": kind,
                        "expected": wanted,
                        "actual": counts[kind],
                    }
                )
    return findings
