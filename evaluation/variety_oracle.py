"""Author-reviewed menu nonregression, independent of selection and parsers."""

from collections import Counter
from collections.abc import Mapping
from typing import Any

ORACLE_VERSION = "authored-variety-nonregression-v1"


def variety_nonregression_findings(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    reviewed: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Compare two menus using separate source reviews, never response scores.

    Args:
        before: Baseline ``menu`` records, each with a nonempty source ID.
        after: Same-input menu records; role, safety and source identity gates
            are separate and must also pass.
        reviewed: ``reviewed_methods``, ``reviewed_declared_families`` and
            ``reviewed_focus`` ID-to-unique-string-list tables, authored by
            reading source recipes. Empty lists mean unestablished evidence.

    Returns:
        Missing/malformed input or count regressions, including lost known
        methods and shifting repetition between methods at equal aggregate
        concentration. Empty means finite nonregression only, not strict gain,
        independent holdout, clinical benefit or overall recommendation quality.
        Unreviewed new IDs fail instead of gaining apparent perfect novelty.

    Example:
        >>> before = {"menu": [{"recipe_id": "a"}]}
        >>> reviews = {key: {"a": []} for key in (
        ...     "reviewed_methods", "reviewed_declared_families", "reviewed_focus")}
        >>> variety_nonregression_findings(before, before, reviews)
        []
    """
    tables: dict[str, Mapping[str, list[str]]] = {}
    for key in ("reviewed_methods", "reviewed_declared_families", "reviewed_focus"):
        table = reviewed.get(key) if isinstance(reviewed, Mapping) else None
        if (
            not isinstance(table, Mapping)
            or not table
            or any(
                not isinstance(recipe_id, str)
                or not recipe_id.strip()
                or not isinstance(labels, list)
                or any(not isinstance(label, str) or not label.strip() for label in labels)
                or len(labels) != len(set(labels))
                for recipe_id, labels in table.items()
            )
        ):
            return [{"type": "variety_review_missing_or_invalid", "review": key}]
        tables[key] = table
    menus: dict[str, list[str]] = {}
    for phase, row in (("before", before), ("after", after)):
        menu = row.get("menu") if isinstance(row, Mapping) else None
        if (
            not isinstance(menu, list)
            or not menu
            or any(
                not isinstance(r, Mapping)
                or not isinstance(r.get("recipe_id"), str)
                or not r["recipe_id"].strip()
                for r in menu
            )
        ):
            return [{"type": "menu_missing_or_invalid", "phase": phase}]
        menus[phase] = [r["recipe_id"] for r in menu]
        if len(set(menus[phase])) != len(menus[phase]):
            return [{"type": "menu_source_ids_not_unique", "phase": phase}]
    if len(menus["before"]) != len(menus["after"]):
        return [{"type": "menu_count_changed"}]
    findings: list[dict[str, Any]] = []
    for key, table in tables.items():
        missing = sorted(set(menus["before"] + menus["after"]) - table.keys())
        if missing:
            findings.extend(
                {"type": "source_review_missing", "review": key, "recipe_id": recipe_id}
                for recipe_id in missing
            )
            continue
        old = Counter(label for recipe_id in menus["before"] for label in table[recipe_id])
        new = Counter(label for recipe_id in menus["after"] for label in table[recipe_id])
        old_pairs = sum(n * (n - 1) // 2 for n in old.values())
        new_pairs = sum(n * (n - 1) // 2 for n in new.values())
        if new_pairs > old_pairs:
            findings.append(
                {
                    "type": "repeated_pairs_increased",
                    "review": key,
                    "before": old_pairs,
                    "after": new_pairs,
                }
            )
        if key in ("reviewed_methods", "reviewed_declared_families"):
            old_peak, new_peak = max(old.values(), default=0), max(new.values(), default=0)
            if new_peak > old_peak:
                findings.append(
                    {
                        "type": "dominant_count_increased",
                        "review": key,
                        "before": old_peak,
                        "after": new_peak,
                    }
                )
        if key == "reviewed_methods":
            old_known = sum(bool(table[key]) for key in menus["before"])
            new_known = sum(bool(table[key]) for key in menus["after"])
            if new_known < old_known:
                findings.append(
                    {"type": "known_method_coverage_lost", "before": old_known, "after": new_known}
                )
            if len(new) < len(old):
                findings.append(
                    {"type": "distinct_methods_lost", "before": len(old), "after": len(new)}
                )
            for label, n in sorted(new.items()):
                if max(0, n - 1) > max(0, old[label] - 1):
                    findings.append(
                        {
                            "type": "individual_method_repetition_increased",
                            "method": label,
                            "before": old[label],
                            "after": n,
                        }
                    )
    return findings
