"""Conservative non-spicy screening; absence of evidence is not a flavor guarantee."""

import re
from collections.abc import Iterable

_COLOR_PEPPER = re.compile(r"青红椒|青椒|红椒")
_SOURCE_VARIETY = re.compile(
    r"(?P<food>青红椒|青椒|红椒)[（(](?:[青红黄绿](?:色)?)?"
    r"(?:甜椒|彩椒|柿子椒)[）)]"
)


def non_spicy_reasons(
    text: str,
    labels: Iterable[str],
    *,
    ingredient_terms: Iterable[str],
    spicy_labels: Iterable[str],
    uncertain_terms: Iterable[str],
    unparsed: bool,
) -> list[str]:
    """Return rejection evidence for one source recipe.

    Args:
        text: Actual raw ingredients, parsed ingredients and cooking steps.
        labels: Normalized descriptive flavor labels, not medical claims.
        ingredient_terms: Configured chili/pungent ingredients and aliases.
        spicy_labels: Exact positive flavor labels; never substring matches.
        uncertain_terms: Compounds whose written composition is not verified.
        unparsed: Whether ingredient parsing is incomplete.

    Returns:
        Empty when the finite checks pass; otherwise concrete rejection reasons.
        Optional chili additions are not silently removed from source recipes.
    """
    compact_text = re.sub(r"\s+", "", text).casefold()
    matched = sorted({term for term in ingredient_terms if term and term in compact_text})
    flavors = sorted(set(labels) & set(spicy_labels))
    uncertain = sorted({term for term in uncertain_terms if term and term in compact_text})
    # A color is not a cultivar or proof of mildness. An explicitly bound
    # source variety can resolve that same food's shorthand in parsed names
    # and steps; an unrelated 彩椒, a label or an optional substitution cannot.
    declared_mild = {match["food"] for match in _SOURCE_VARIETY.finditer(compact_text)}
    unknown_peppers = sorted({
        match.group() for match in _COLOR_PEPPER.finditer(compact_text)
        if match.group() not in declared_mild
    })
    reasons: list[str] = []
    if matched:
        reasons.append("不辣要求命中辣味配料：" + "、".join(matched))
    if flavors:
        reasons.append("不辣要求存在显式辣味标签：" + "、".join(flavors))
    if uncertain:
        reasons.append("不辣要求存在成分不明配料，无法确认辣味：" + "、".join(uncertain))
    if unknown_peppers:
        reasons.append("不辣要求存在品种未明确的椒类，无法确认不辣：" + "、".join(unknown_peppers))
    if unparsed:
        reasons.append("食材解析不完整，无法核对不辣要求。")
    return reasons
