"""Explicit finite method references, separate from implicit variety defaults."""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from app.domain.advance_preparation import is_advance_preparation_permission
from app.domain.cooking_methods import main_cooking_methods
from app.domain.entree_preferences import joint_entree_variety_clause
from app.domain.models import Recipe, ScopedMethod
from app.domain.scoped_methods import scoped_method_coverage

METHOD_PREFERENCE_VERSION = "explicit-source-method-reference-v2"
_ALIASES = {
    "蒸": "蒸",
    "清蒸": "蒸",
    "蒸制": "蒸",
    "煮": "煮",
    "炖": "炖",
    "炒": "炒",
    "煸炒": "炒",
    "烤": "烤",
    "烘烤": "烤",
    "煎": "煎",
    "炸": "炸",
    "油炸": "炸",
    "烧": "烧",
    "红烧": "烧",
    "焖": "焖",
    "拌": "拌",
    "凉拌": "拌",
}
_TERMS = "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True))
_SPLIT = re.compile(r"[、,，;；。.!！\r\n]+|不过|但是|可是|然而|而是|但")
_MENTION = re.compile(r"如果|假如|例如|比如|解释|为什么|什么叫|是否|[?？\"'“”‘’]")
_PREFIX = r"(?:(?:我|我们|这餐|本餐)?(?:想要|想吃|要|希望|喜欢|偏好|注意|尽量)?|做法[:：])"
_NEGATIVE = r"(?P<negative>不要|不喜欢|不想要|避免|别)?"
_REQUEST = re.compile(
    r"^" + _PREFIX + _NEGATIVE + r"(?P<method>" + _TERMS + r")(?:菜|的菜|做法)?(?:优先|就好)?$"
)
_DIVERSITY = re.compile(r"^" + _PREFIX + _NEGATIVE + r"做法(?:多样|丰富|多样性)$")
_UNKNOWN = re.compile(r"^做法[:：](?P<text>[^:：]{1,40})$")


@dataclass(frozen=True)
class MethodRequests:
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    diversity: bool
    negative_diversity: bool
    unknown: tuple[str, ...]


def method_request_clauses(text: str) -> tuple[str, ...]:
    """Ground bounded literal clauses only; questions and quotations do not update."""
    if _MENTION.search(text):
        return ()
    result: list[str] = []
    for part in _SPLIT.split(text):
        part = "".join(part.split())
        if joint_entree_variety_clause(part) or part in {"做法：多样", "做法：丰富", "做法：多样性"}:
            value = "做法多样"
        elif part in {"做法：不要多样", "做法：不要丰富", "做法：不要多样性"}:
            value = "不要做法多样"
        elif match := _REQUEST.fullmatch(part):
            value = "做法：" + ("不要" if match["negative"] else "") + _ALIASES[match["method"]]
        elif match := _DIVERSITY.fullmatch(part):
            value = ("不要" if match["negative"] else "") + "做法多样"
        elif _UNKNOWN.fullmatch(part):
            value = part
        else:
            continue
        if value not in result:
            result.append(value)
    return tuple(result)


@lru_cache(maxsize=4096)
def _requests(values: tuple[str, ...]) -> MethodRequests:
    positive: list[str] = []
    negative: list[str] = []
    unknown: list[str] = []
    diversity = negative_diversity = False
    for value in values:
        for clause in method_request_clauses(value):
            if is_advance_preparation_permission(clause):
                continue
            if clause == "做法多样":
                diversity = True
            elif clause == "不要做法多样":
                negative_diversity = True
            elif match := _REQUEST.fullmatch(clause):
                target = negative if match["negative"] else positive
                method = _ALIASES[match["method"]]
                if method not in target:
                    target.append(method)
            elif clause not in unknown:
                unknown.append(clause)
    return MethodRequests(
        tuple(positive), tuple(negative), diversity, negative_diversity, tuple(unknown)
    )


def method_requests(preferences: Iterable[str]) -> MethodRequests:
    return _requests(tuple(preferences))


def supported_method_preferences(preferences: Iterable[str]) -> tuple[str, ...]:
    requests = method_requests(preferences)
    return tuple(method for method in requests.positive if method not in requests.negative)


def method_reference_mask(recipe: Recipe, preferences: Iterable[str]) -> int:
    requested = supported_method_preferences(preferences)
    return (
        sum(1 << i for i, method in enumerate(requested) if method in main_cooking_methods(recipe))
        if requested
        else 0
    )


def method_spread(menu: Sequence[Recipe]) -> tuple[int, int, int, int]:
    """Known dish count, distinct methods, max frequency, repeated pairs."""
    methods = [main_cooking_methods(recipe) for recipe in menu]
    counts = Counter(method for values in methods for method in values)
    return (
        sum(bool(values) for values in methods),
        len(counts),
        max(counts.values(), default=0),
        sum(n * (n - 1) // 2 for n in counts.values()),
    )


def method_swap_preserves(
    menu: Sequence[Recipe], index: int, candidate: Recipe, preferences: Iterable[str],
    *, protect_requested: bool = True,
    scoped: Sequence[ScopedMethod] = (),
    protect_diversity: bool = True,
) -> bool:
    """Protect explicit reference coverage/diversity, not unrequested old methods.

    Specific positive requests protect each already-covered method type, not
    every old method or its identity/frequency. Explicit diversity protects
    known coverage, distinct count and aggregate concentration; switching the
    identities of equally diverse methods is permitted. Negative methods and
    contradictions remain disclosed, not silently upgraded to safety rules.
    protect_diversity=False waives only generic spread proxies; named/scoped
    method coverage remains protected. Callers must authorize and disclose it.
    """
    values = tuple(preferences)
    if scoped:
        proposed = [*menu[:index], candidate, *menu[index + 1 :]]
        before_scoped = scoped_method_coverage(menu, scoped)
        if scoped_method_coverage(proposed, scoped) & before_scoped != before_scoped:
            return False
    requests = method_requests(values)
    positive = supported_method_preferences(values)
    diversity = requests.diversity and not requests.negative_diversity
    if not positive and not diversity:
        return True
    proposed = [*menu[:index], candidate, *menu[index + 1 :]]
    before = {method for recipe in menu for method in main_cooking_methods(recipe)}
    after = {method for recipe in proposed for method in main_cooking_methods(recipe)}
    if protect_requested and not (before.intersection(positive) <= after):
        return False
    if diversity and protect_diversity:
        old, new = method_spread(menu), method_spread(proposed)
        if new[0] < old[0] or new[1] < old[1] or new[2] > old[2] or new[3] > old[3]:
            return False
    return True


def method_preference_warnings(menu: Sequence[Recipe], preferences: Iterable[str]) -> list[str]:
    values = tuple(preferences)
    requests = method_requests(values)
    if not (
        requests.positive
        or requests.negative
        or requests.diversity
        or requests.negative_diversity
        or requests.unknown
    ):
        return []
    warnings = []
    conflict = set(requests.positive).intersection(requests.negative)
    if requests.diversity and requests.negative_diversity:
        conflict.add("做法多样")
    if conflict:
        warnings.append(
            "明确做法要求存在冲突："
            + "、".join(sorted(conflict))
            + "；需要确认，不擅自解除历史要求。"
        )
    known = {method for recipe in menu for method in main_cooking_methods(recipe)}
    missing = [method for method in supported_method_preferences(values) if method not in known]
    if missing:
        warnings.append(
            "明确做法来源参考尚缺："
            + "、".join(missing)
            + "；当前安全候选、已覆盖需求或本轮换菜范围内未补齐。"
        )
    if requests.negative or requests.negative_diversity:
        warnings.append(
            "负向做法要求已记录；本轮未实现完整做法排除保证，未知步骤不能证明已避开；不辣与过敏仍独立校验。"
        )
    if requests.unknown:
        warnings.append("做法要求暂无可靠映射：" + "、".join(requests.unknown) + "；不能宣称满足。")
    if requests.diversity and not requests.negative_diversity:
        spread = method_spread(menu)
        warnings.append(
            f"明确做法多样性要求的源观察：已知成菜动作 {spread[0]}/{len(menu)} 道、{spread[1]} 种；不是充分多样性或实际烹饪质量验收。"
        )
    warnings.append(
        "做法匹配仅采用原步骤中有限、明确的成菜动作；不是设备/缓存标签、整餐全部采用该做法、用油量或健康效果保证。"
    )
    return warnings
