"""Independent authored finishing-method comparisons, never response scores."""

from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.focus_oracle import culinary_focus_findings

ORACLE_VERSION = "authored-finishing-method-v1"


def cooking_method_findings(
    result: Mapping[str, Any],
    expected: Mapping[str, Any],
    observed: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    """Compare source methods with a separately authored ID-bound review table.

    Args:
        result: Menu and optional suggestions with source identities.
        expected: ``reviewed_methods`` maps each emitted ID to unique method
            strings. Empty is unestablished evidence, never perfect diversity.
        observed: Methods read from the evaluated source, not response claims.

    Returns:
        Missing, malformed or mismatching findings. Reuses only the pure
        string-list comparison contract, never the production action parser.
        Agreement is finite source consistency, not overall quality, oil amounts
        or clinical evidence. Identity and hard gates must be checked separately.

    Example:
        >>> result = {"menu": [{"recipe_id": "r"}]}
        >>> expected = {"reviewed_methods": {"r": ["蒸"]}}
        >>> cooking_method_findings(result, expected, {"r": ["蒸"]})
        []
        >>> bool(cooking_method_findings(result, expected, {"r": ["烤"]}))
        True
    """
    reviews = expected.get("reviewed_methods") if isinstance(expected, Mapping) else None
    findings = culinary_focus_findings(result, {"reviewed_focus": reviews}, observed)
    return [
        {
            key.replace("focus", "methods"): (
                value.replace("focus", "methods") if key == "type" else value
            )
            for key, value in finding.items()
        }
        for finding in findings
    ]
