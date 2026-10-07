"""Whole-meal diet checks against authored source review, not service verdicts."""

from collections.abc import Mapping, Sequence
from typing import Any

ORACLE_VERSION = "authored-whole-meal-diet-oracle-v1"


def diet_findings(
    menu: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    suggestions: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Check every output, including soup/staple and replacement proposals.

    Reviewer-owned source kinds are plant, egg_dairy_honey, meat or unknown.
    No production classifier is imported. Reviews must be authored from the
    same source snapshot; source-ID/content authenticity is a separate gate.
    This does not certify brands, unlisted ingredients or cross-contact.
    """
    mode = expected.get("mode")
    index = expected.get("reviewed_source_kinds")
    valid = {"plant", "egg_dairy_honey", "meat", "unknown"}
    if (
        not isinstance(mode, str)
        or mode not in {"ovo_lacto_vegetarian", "vegan"}
        or not isinstance(index, Mapping)
        or not index
        or any(
            not isinstance(key, str) or not isinstance(kind, str) or kind not in valid
            for key, kind in index.items()
        )
    ):
        return [{"type": "diet_review_missing_or_invalid"}]
    findings: list[dict[str, Any]] = []
    if not menu:
        findings.append({"type": "menu_missing"})
    allowed = {"plant", "egg_dairy_honey"} if mode == "ovo_lacto_vegetarian" else {"plant"}
    for name, items in (("menu", menu), ("suggestions", suggestions)):
        for item in items:
            key = str(item.get("recipe_id", ""))
            kind = index.get(key)
            if kind not in allowed:
                findings.append(
                    {
                        "output": name,
                        "recipe_id": key,
                        "type": "source_review_missing" if kind is None else "whole_diet_violation",
                        "source_kind": kind,
                        "mode": mode,
                    }
                )
    return findings
