"""Shared finite declared-food vocabulary, not allergens, mass or a dish role.

Consumers still enforce their distinct culinary-role and authorization gates.
Unknown compounds do not acquire a whole-food proof by substring matching.
"""

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType

VERSION = "shared-declared-protein-food-names-v2-literal-count"
PROTEIN_FOOD_SPEC: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = MappingProxyType(
    {
        "鸡蛋": (
            ("蛋", "鸡子"),
            ("鸡蛋", "鸡子", "全蛋", "全蛋液", "蛋液", "蛋清", "蛋黄", "鸡蛋清", "鸡蛋黄"),
        ),
        "鱼": (
            ("鱼", "鲈", "鲳", "鳕", "鳝", "三文", "石斑", "东星斑", "龙利", "多宝"),
            (
                "鱼",
                "鱼肉",
                "鱼片",
                "鱼块",
                "鲈鱼",
                "草鱼",
                "鳕鱼",
                "黄鱼",
                "黄花鱼",
                "带鱼",
                "三文鱼",
                "鲫鱼",
                "鲤鱼",
                "鳊鱼",
                "鲳鱼",
                "银鲳",
                "银鳕鱼",
                "鳝鱼",
                "石斑鱼",
                "东星斑",
                "龙利鱼",
                "多宝鱼",
                "巴沙鱼",
            ),
        ),
        "虾": (
            ("虾",),
            ("虾", "虾仁", "鲜虾", "大虾", "青虾", "基围虾", "白虾", "河虾", "海虾", "对虾"),
        ),
        "鸡肉": (
            ("鸡", "凤爪"),
            (
                "鸡肉",
                "鸡胸肉",
                "鸡腿肉",
                "鸡脯肉",
                "鸡胸",
                "鸡腿",
                "鸡翅",
                "鸡中翅",
                "鸡全翅",
                "整鸡",
                "全鸡",
                "三黄鸡",
                "乌鸡",
                "仔鸡",
                "童子鸡",
                "土鸡",
                "嫩鸡",
                "鸡",
            ),
        ),
        "猪肉": (
            ("猪", "肉", "肘", "里脊", "蹄", "排骨"),
            (
                "猪肉",
                "猪瘦肉",
                "猪肘子",
                "猪里脊肉",
                "猪梅花肉",
                "猪五花肉",
                "五花肉",
                "猪排骨",
                "猪蹄",
                "猪腿肉",
            ),
        ),
        "牛肉": (
            ("牛",),
            (
                "牛肉",
                "牛腩",
                "牛腩肉",
                "牛排",
                "牛里脊",
                "牛里脊肉",
                "牛柳",
                "牛腱",
                "牛腱肉",
                "牛腱子",
                "牛腱子肉",
                "肥牛",
            ),
        ),
        "鸭肉": (("鸭",), ("鸭肉", "鸭腿", "鸭胸肉", "鸭胸", "整鸭", "全鸭", "鸭")),
        "豆腐": (("豆腐",), ("豆腐", "老豆腐", "北豆腐", "南豆腐", "嫩豆腐", "水豆腐", "内酯豆腐")),
    }
)
ANIMAL_PROTEIN_FOODS = frozenset({"鱼", "虾", "鸡肉", "猪肉", "牛肉", "鸭肉"})
_PREFIX = r"(?:新鲜|鲜活|鲜|活|净|冷冻|冰冻|去骨|去皮|带皮|带骨|切碎|切片)*"
_SUFFIX = r"(?:块|片|丝|末|丁|段)*"
_COUNT_SUFFIX = r"(?:[一二两三四五六七八九十](?:条|只|个|块|片))?"


def whole_food_pattern(names: Iterable[str]) -> re.Pattern[str]:
    """Complete known body plus finite descriptors/literal source item counts.

    Count suffixes only witness an explicitly declared food, never convert
    items into grams or fill the original Ingredient quantity/unit fields.
    Unknown compounds and alternatives still fail the whole-string match.
    """
    return re.compile(
        _PREFIX + "(?:" + "|".join(map(re.escape, names)) + ")" + _SUFFIX + _COUNT_SUFFIX
    )


_DECLARED = {key: whole_food_pattern(names) for key, (_markers, names) in PROTEIN_FOOD_SPEC.items()}


def declared_protein_foods(food: str) -> frozenset[str]:
    """Whole parsed ingredient references, not title, role or dietary proof."""
    name = "".join(food.split())
    return frozenset(key for key, pattern in _DECLARED.items() if pattern.fullmatch(name))


@lru_cache(maxsize=8192)
def named_declared_protein_foods(name: str, foods: tuple[str, ...]) -> frozenset[str]:
    # Import only the pure name masker; no Recipe, classifier or planner cycle.
    from app.domain.food_names import without_plant_animal_homonyms

    title = without_plant_animal_homonyms("".join(name.split())).split("配", 1)[0]
    title = re.sub(r"^(?:蛋黄|蛋液|虾皮|虾酱|虾粉|鱼露|鱼汤|鸡汤|猪油|鸡油|鸭油)", "", title)
    for flavor_or_stock in (
        "鱼香",
        "鱼露",
        "鱼汤",
        "鸡汤",
        "鸡精",
        "鸡粉",
        "蛋黄酱",
        "虾酱",
        "虾粉",
        "虾皮",
    ):
        title = title.replace(flavor_or_stock, "")
    return frozenset(
        key
        for key, (markers, _names) in PROTEIN_FOOD_SPEC.items()
        if any(marker in title for marker in markers)
        and any(_DECLARED[key].fullmatch("".join(food.split())) for food in foods)
    )
