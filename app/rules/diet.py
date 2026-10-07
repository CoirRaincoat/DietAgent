"""Whole-recipe declared diet checks, including soup, staple and step additions."""

from app.domain.dish_composition import non_meat_source_kind
from app.domain.models import Constraints, Recipe

DIET_VERSION = "whole-recipe-diet-text-v1"
VEGAN_FORBIDDEN = (
    "鸡蛋",
    "鸭蛋",
    "鹅蛋",
    "鹌鹑蛋",
    "蛋清",
    "蛋黄",
    "全蛋",
    "蛋液",
    "牛奶",
    "羊奶",
    "奶粉",
    "奶酪",
    "芝士",
    "黄油",
    "酸奶",
    "奶油",
    "乳酪",
    "炼乳",
    "蜂蜜",
    "蜂蜡",
    "明胶",
    "鱼胶",
    "吉利丁",
    "蛋",
)


def diet_reasons(recipe: Recipe, constraints: Constraints) -> list[str]:
    """Reject incompatible or unknown declared sources, not just entrée classes.

    Args:
        recipe: Unmodified source recipe, including ingredient names and steps.
        constraints: Whole-meal diet mode; omnivore adds no diet restriction.

    Returns:
        Finite reasons for refusal. Empty means text screening passed only:
        brands, undeclared ingredients and cross-contact remain unverified.
    """
    if constraints.diet_mode == "omnivore":
        return []
    kind = non_meat_source_kind(recipe)
    if kind == "meat":
        return ["整餐素食限制命中源配料或步骤中的肉、鱼虾或动物肉源；汤和主食同样核对。"]
    if kind == "other":
        return ["复合配料、步骤来源或食材解析不完整，无法按当前整餐素食模式核对。"]
    if constraints.diet_mode == "vegan":
        source = ("、".join(i.name for i in recipe.ingredients) + recipe.steps).replace(
            "蛋白质", ""
        )
        matched = [term for term in VEGAN_FORBIDDEN if term in source]
        if matched:
            return ["整餐纯素限制命中源配料或步骤：" + "、".join(matched) + "。"]
    return []


def diet_issue(constraints: Constraints) -> str | None:
    """Do not silently discard either whole-meal diet or positive meat quotas."""
    if constraints.diet_mode != "omnivore" and ((constraints.meat_dish_count or 0) > 0 or (constraints.meat_soup_count or 0) > 0):
        quantities = []
        if (constraints.meat_dish_count or 0) > 0:
            quantities.append("荤菜数量")
        if (constraints.meat_soup_count or 0) > 0:
            quantities.append("荤汤数量")
        label = "、".join(quantities)
        return f"整餐素食模式与明确{label}冲突，请确认饮食模式或{label}；不会自动撤销已确认要求。"
    return None
