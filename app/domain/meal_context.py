"""Finite source context evidence, not clinical suitability or nutritional doses."""

import re
from collections.abc import Iterable, Sequence
from typing import Literal

from app.domain.meal_references import MEAL_REFERENCES, meal_reference
from app.domain.models import Recipe

CONTEXT_VERSION = "ordinary-meal-source-context-v2-assistant-references"
MealFit = Literal["matched", "suggested", "unknown", "other_meal"]
_MEALS = frozenset({"早餐", "午餐", "晚餐", "下午茶", "夜宵"})
MEAL_TAGS = _MEALS
_MEAL_EXCLUSION = re.compile(
    r"^(?:(?:这餐|本餐|这次|今天|明天|我|我们)(?:要)?|也)?"
    r"(?:不要|不喜欢|不想要|避免|别)(?P<meal>早餐|午餐|晚餐|下午茶|夜宵)"
    r"(?:风格|菜|菜肴|菜单|餐次标签)?(?:即可|就好)?$"
)
_MEAL_MENTION = re.compile(r"如果|假如|例如|比如|解释|为什么|什么叫|是否|能否|[?？\"'“”‘’]")


def negative_meal_request_clauses(text: str) -> tuple[str, ...]:
    """A negative recipe-context request never sets the current meal type.

    Only standalone finite exclusions are grounded. Attributed statements,
    quotes, double negation, cancellations and bare meal mentions are not new
    restrictions. This is not an interpretation of every '不要吃夜宵' intent.
    """
    if _MEAL_MENTION.search(text):
        return ()
    found = []
    for part in re.split(r"[、,，;；。.!！\r\n]+|不过|但是|可是|然而|而是|但", text):
        if match := _MEAL_EXCLUSION.fullmatch(re.sub(r"\s+", "", part)):
            value = "不要" + match["meal"]
            if value not in found:
                found.append(value)
    return tuple(found)


def supported_meal_exclusions(preferences: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        clause.removeprefix("不要")
        for preference in preferences for clause in negative_meal_request_clauses(preference)
    ))
_BABY = re.compile(
    r"宝宝辅食|婴儿(?:辅食|专用)|婴幼儿辅食|(?:给|供|适合)(?:小)?(?:宝宝|婴儿)(?:食用|吃)|(?:宝宝|婴儿)专用"
)
_NEGATIVE = re.compile(r"不(?:适合|建议|可|能)(?:给|供)?(?:小)?(?:宝宝|婴儿)(?:食用|吃)?")
_FAMILY = re.compile(r"成人(?:也)?(?:可|能)食用|全家(?:都|均|也)?(?:可|能)食用")


def infant_only_source(recipe: Recipe) -> bool:
    """Exclude explicit baby-targeted preparations from the ordinary-menu path.

    A baby's name alone is insufficient. Negated suitability and explicit
    family use do not prove infant-only intent. This does not certify feeding
    safety for children or infer every diner's age from the profile owner.
    """
    text = "。".join((recipe.name, recipe.raw_label, recipe.steps))
    if _FAMILY.search(text):
        return False
    return bool(_BABY.search(_NEGATIVE.sub("", text)))


def source_meals(recipe: Recipe) -> frozenset[str]:
    """Read exact source labels; never promote health prose or stale cache tags."""
    if recipe.raw_label.strip():
        tokens = [token.strip() for token in re.split(r"[、,，;；\n]+", recipe.raw_label)]
        return frozenset(token for token in tokens if token in _MEALS)
    return frozenset(tag for tag in recipe.meal_types if tag in _MEALS)


def meal_fit(recipe: Recipe, requested: str) -> MealFit:
    """Distinguish evidence matching, absent metadata and other-meal metadata."""
    tags = source_meals(recipe)
    if requested in tags:
        return "matched"
    if tags:
        return "other_meal"
    reference = meal_reference(recipe)
    if reference is not None and requested in reference.meals:
        return "suggested"
    # Absence of an assistant suggestion is NOT a negative suitability label.
    return "unknown"


def meal_cost(recipe: Recipe, requested: str) -> int:
    """Bounded ordinal preference, not a quality score or hard safety rule."""
    return {"matched": 0, "suggested": 1, "unknown": 2, "other_meal": 3}[
        meal_fit(recipe, requested)
    ]


def meal_context_warnings(menu: Sequence[Recipe], requested: str) -> list[str]:
    """Keep metadata gaps visible, including when a protected slot cannot move."""
    mismatched = [r.name for r in menu if meal_fit(r, requested) == "other_meal"]
    unknown = [r.name for r in menu if meal_fit(r, requested) == "unknown"]
    warnings = []
    for recipe in menu:
        reference = meal_reference(recipe)
        if meal_fit(recipe, requested) == "suggested" and reference is not None:
            warnings.append(
                f"餐次适配尚未核验：{recipe.name}采用助手餐次参考「{requested}」；"
                f"依据原配方：{reference.rationale}原餐次标签仍缺失，"
                "参考不是独立审核、健康功效或整餐份量证明。"
            )
        elif recipe.recipe_id in MEAL_REFERENCES and reference is None:
            warnings.append(f"{recipe.name}的助手餐次参考与当前配方不一致，未采用；需重新核对。")
    if mismatched:
        warnings.append(
            "餐次标签未匹配「"
            + requested
            + "」："
            + "、".join(mismatched)
            + "；源标签仅列其他餐次，当前候选或换菜范围内未替换。标签不是禁食或营养证明。"
        )
    if unknown:
        warnings.append(
            "餐次适配尚未核验："
            + "、".join(unknown)
            + "；源餐次标签缺失，不据此判为不适合，也不声称已验证。"
        )
    return warnings
