"""Finite declared-food families for variety only, never allergy or nutrient rules."""

import re
from functools import lru_cache

from app.domain.models import Recipe

FAMILY_VERSION = "declared-food-variety-v4"
_ALIASES: dict[str, tuple[str, ...]] = {
    "pumpkin": ("贝贝南瓜", "南瓜"),
    "broccoli": ("西兰花", "西蓝花"),
    "winter_melon": ("冬瓜",),
    "yam": ("山药", "淮山", "菜山药"),
    "rice": ("大米", "粳米", "籼米", "米饭", "米"),
    "millet": ("小米",),
    "oats": ("燕麦", "燕麦片"),
    "wheat": ("面粉", "小麦粉", "低筋面粉", "高筋面粉", "中筋面粉"),
    "chicken": ("鸡肉", "鸡胸肉", "鸡腿肉", "鸡腿", "鸡翅", "三黄鸡"),
    "pork": ("猪肉", "猪里脊肉", "猪肉末", "猪肉馅", "猪肉片", "五花肉", "猪肘子"),
    "egg": ("鸡蛋", "鲜鸡蛋", "鸡蛋液", "蛋黄", "蛋白"),
    "tofu": ("豆腐", "豆腐皮", "豆腐衣", "千张", "百叶", "薄百叶", "豆皮", "内酯豆腐", "南豆腐"),
    "cabbage": ("白菜", "大白菜", "娃娃菜"),
    "spinach": ("菠菜",),
    "tomato": ("番茄", "西红柿"),
    "carrot": ("胡萝卜",),
    "potato": ("土豆", "马铃薯"),
    "sweet_potato": ("红薯", "紫薯", "番薯"),
}
_PATTERNS = {
    family: re.compile(
        r"(?:熟|生|鲜|老|嫩)?(?:" + "|".join(map(re.escape, aliases)) + r")(?:块|片|泥|丁|丝|粒)?"
    )
    for family, aliases in _ALIASES.items()
}


@lru_cache(maxsize=8192)
def _declared_families(names: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        family
        for family, pattern in _PATTERNS.items()
        if any(pattern.fullmatch(name) for name in names)
    )


def food_families(recipe: Recipe) -> frozenset[str]:
    """Read finite family evidence from parsed ingredient declarations.

    Args:
        recipe: Source recipe with ingredient names. Titles do not supply foods.

    Returns:
        Known culinary families, without amounts or dominant-food inference.
        Exact finite aliases allow cooked/cut forms, not sauces, oils or powders.
        Empty means no recognized evidence, not perfect novelty. This mapping
        must never normalize hard restrictions, allergens or nutrition facts.
        Immutable names key the cache so edited source records are rechecked.
    """
    return _declared_families(tuple(item.name.strip() for item in recipe.ingredients))
