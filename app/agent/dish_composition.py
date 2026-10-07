"""Ground explicit culinary composition text and construct quota-feasible menus."""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.agent.menu_structure import _number
from app.domain.dish_composition import dish_kind, non_meat_source_kind
from app.domain.models import Constraints, Recipe

_NUMBER = r"[0-9零〇一二两三四五六七八九十]+"
_MEASURE = r"(?:道|个|款)?(?:独立(?:的)?)?"
_MATCH = re.compile(
    rf"(?<![第0-9一二两三四五六七八九十])(?P<n>{_NUMBER})"
    rf"{_MEASURE}(?P<kind>荤|素)(?!菜?汤)(?:菜)?"
)
_ISSUE = "请明确荤菜、素菜各几道；本口径不含汤和主食，素菜允许蛋奶。"


def explicit_dish_composition(message: str) -> tuple[dict[str, int], str | None]:
    """Read finite entree counts, including explicit independent-dish wording.

    Qualified soups never fill these non-soup quotas. Questions, ranges and
    negations still require confirmation instead of resolving themselves.
    """
    text = re.sub(r"\s+", "", message)
    if any(c in text for c in "“”\"'「」") or re.search(r"解释|上次|以前|比如|例如", text):
        return {}, None
    matches = list(_MATCH.finditer(text))
    if not matches:
        return {}, None
    if (
        re.search(r"如果|假如|或者|还是|是否|吗|不是|[?？]", text)
        or re.search(rf"(?:不要|不用|不需要){_NUMBER}{_MEASURE}(?:荤|素)", text)
        or re.search(rf"{_NUMBER}(?:到|至|[-~～]){_NUMBER}{_MEASURE}(?:荤|素)", text)
    ):
        return {}, _ISSUE
    counts: dict[str, int] = {}
    try:
        for kind, field in (("荤", "meat_dish_count"), ("素", "vegetarian_dish_count")):
            values = {_number(m["n"]) for m in matches if m["kind"] == kind}
            if len(values) > 1 or any(not 0 <= v <= 8 for v in values):
                return {}, _ISSUE
            if values:
                counts[field] = values.pop()
    except ValueError:
        return {}, _ISSUE
    return counts, None


def independent_meat_requested(message: str) -> bool:
    """Only confirmed literal independent meat quantities tighten meal scope.

    No profile/LLM preference or standalone adjective grants this authority.
    The quantity parser's quotation, negation, ambiguity and conflict guards
    apply first; ordinary follow-ups cannot silently weaken an existing scope.
    """
    counts, issue = explicit_dish_composition(message)
    if issue is not None or "meat_dish_count" not in counts:
        return False
    text = re.sub(r"\s+", "", message)
    return any(m["kind"] == "荤" and "独立" in m[0] for m in _MATCH.finditer(text))


@dataclass(frozen=True)
class CompositionPlan:
    """Exact-quantity planning outcome; failure never returns an unverified menu."""

    recipes: list[Recipe]
    failure: str | None = None


def plan_composition(
    candidates: Sequence[Recipe],
    constraints: Constraints,
    current: Sequence[Recipe],
    *,
    choose: Callable[[list[Recipe], list[Recipe]], Recipe],
    replace_slot: int | None = None,
    choose_slot: Callable[[list[Recipe], list[Recipe], int], Recipe] | None = None,
    edit_slots: set[int] | None = None,
) -> CompositionPlan:
    """Preserve feasible old slots, then choose with disjoint-group look-ahead.

    Args:
        candidates: Unique, already hard-screened, meal-eligible recipes.
        constraints: Total, soup, and explicit meat/no-meat quotas.
        current: Previous source recipes in slot order.
        choose: Existing suitability ranker, restricted to feasible options.
        replace_slot: Protect all other slots for a single replacement.

    Returns:
        Exact quota-feasible menu or an explicit bounded-catalog/scope failure.
        Six disjoint entree/soup-source kinds make feasibility a capacity calculation,
        not a greedy guess. This does not optimize all ingredient preferences
        jointly or claim globally minimal edits.
    """
    pool = {r.recipe_id: r for r in candidates}
    if replace_slot is not None and 1 <= replace_slot <= len(current):
        pool.pop(current[replace_slot - 1].recipe_id, None)
    soup_groups = ("soup_meat", "soup_vegetarian", "soup_other")
    kinds = {
        key: "soup_" + non_meat_source_kind(r) if "soup" in r.categories else dish_kind(r, constraints)
        for key, r in pool.items()
    }
    capacities: Counter[str] = Counter(kinds.values())
    targets = {
        "meat": constraints.meat_dish_count,
        "vegetarian": constraints.vegetarian_dish_count,
        "soup_meat": constraints.meat_soup_count,
        "soup_vegetarian": constraints.vegetarian_soup_count,
    }

    def capacity_fits(groups: Sequence[str], counts: Counter[str], available: Counter[str], slots: int) -> bool:
        required = 0
        capacity = 0
        for kind in groups:
            target = targets.get(kind)
            if target is None:
                capacity += available[kind]
            else:
                needed = target - counts[kind]
                if needed < 0 or available[kind] < needed:
                    return False
                required += needed
                capacity += needed
        return required <= slots <= capacity

    def can_finish(selected: list[Recipe]) -> bool:
        counts: Counter[str] = Counter(kinds[r.recipe_id] for r in selected)
        available = capacities - counts
        if any(target is not None and counts[kind] > target for kind, target in targets.items()):
            return False
        soup_needed = constraints.soup_count - sum(counts[k] for k in soup_groups)
        food_slots = constraints.dish_count - len(selected) - soup_needed
        if soup_needed < 0 or food_slots < 0:
            return False
        return capacity_fits(soup_groups, counts, available, soup_needed) and capacity_fits(
            ("meat", "vegetarian", "other"), counts, available, food_slots
        )

    failure = "当前安全候选无法满足指定荤菜/素菜数量；成分不明菜、汤和主食不冒充荤素菜位。"
    if constraints.meat_dish_scope == "independent_entree":
        failure += "独立荤菜另需有限肉鱼主体来源，含肉点缀的蔬菜、肉汤或虾皮调味不能填该数量。"
    if constraints.meat_soup_count is not None or constraints.vegetarian_soup_count is not None:
        failure += "明确荤汤/素汤数量另核对源食材与步骤，未知复合配料不能冒充素汤。"
    if not can_finish([]):
        return CompositionPlan([], failure)
    slots: list[Recipe | None] = [None] * constraints.dish_count
    for index, old in enumerate(current[: constraints.dish_count]):
        selected = [r for r in slots if r is not None]
        protect = (replace_slot is not None and index != replace_slot - 1
                   or edit_slots is not None and index + 1 not in edit_slots)
        if (
            old.recipe_id in pool
            and old.recipe_id not in {r.recipe_id for r in selected}
            and can_finish([*selected, pool[old.recipe_id]])
        ):
            slots[index] = pool[old.recipe_id]
        elif protect:
            return CompositionPlan(
                [], "仅换指定菜无法同时满足新的荤素数量和其他槽位不变，请确认是否允许整餐调整。"
            )
    for index, existing in enumerate(slots):
        if existing is not None:
            continue
        selected = [r for r in slots if r is not None]
        used = {r.recipe_id for r in selected}
        options = [r for key, r in pool.items() if key not in used and can_finish([*selected, r])]
        if not options:
            return CompositionPlan([], failure)
        slots[index] = choose_slot(options, selected, index + 1) if choose_slot else choose(options, selected)
    return CompositionPlan([r for r in slots if r is not None])
