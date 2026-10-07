"""Finite new-meal commands and recommendation exposure, never consumption."""

import re
from collections import Counter
from collections.abc import Sequence

from app.rules.engine import compact

HISTORY_LIMIT = 8
_NEXT = (
    r"(?:下一餐|新一餐|新的一餐|另一餐|明天(?:的)?(?:早餐|午餐|晚餐)|后天(?:的)?(?:早餐|午餐|晚餐))"
)
_COMMAND = re.compile(
    rf"(?:请|帮我)?(?:重新)?(?:安排|推荐|规划)(?:一下)?{_NEXT}(?:的)?(?:菜单)?[。.!！]?"
)


def explicit_new_meal(message: str) -> bool:
    """Only a complete affirmative command clause grants a new meal boundary.

    Mere date words, questions, quotations, replacement clauses and combined
    alternatives do not grant scope. The caller also requires a plan intent.
    This is a deliberately finite contract, not general temporal extraction.
    """
    if re.search(
        r"[?？\"'“”‘’「」]|(?:如果|假如|假设|例如|比如|引用|他说|她说)|(?:不要|不用|不想|暂不)(?:重新)?(?:安排|推荐|规划)",
        message,
    ):
        return False
    # A command embedded after a speaker/source prefix is not user authority.
    first = re.split(r"[，,；;\n]", message)[0].strip()
    return _COMMAND.fullmatch(first) is not None


def recommendation_counts(menus: Sequence[Sequence[str]]) -> Counter[str]:
    """Count exact source dish names in the last eight recommended menus.

    Each name counts at most once per menu. Oldest menus have weight one,
    latest at most eight. Missing names receive no alias/consumption inference.
    This cost is an engineering recency tie-break, not a quality grade.
    """
    result: Counter[str] = Counter()
    for weight, menu in enumerate(menus[-HISTORY_LIMIT:], 1):
        for name in {compact(v) for v in menu if compact(v)}:
            result[name] += weight
    return result
