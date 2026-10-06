"""Finite source evidence for culinary meat/no-meat dish counts, not vegan safety."""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Literal

from app.domain.food_names import without_plant_animal_homonyms
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_names import ANIMAL_PROTEIN_FOODS, declared_protein_foods

COMPOSITION_VERSION = "source-meat-no-meat-v7-cooked-rice-black-pepper"
DishKind = Literal["meat", "vegetarian", "other", "soup"]
SourceKind = Literal["meat", "vegetarian", "other"]
_ANIMAL = (
    "肉",
    "排骨",
    "肋排",
    "鸡",
    "鸭",
    "鹅",
    "鱼",
    "虾",
    "蟹",
    "牛腩",
    "羊",
    "猪",
    "牛肚",
    "牛筋",
    "牛腱",
    "鲍",
    "蛤",
    "蚬",
    "蚌",
    "牡蛎",
    "蚝",
    "贝",
    "鱿",
    "墨鱼",
    "海参",
    "火腿",
    "培根",
    "腊肠",
    "香肠",
    "花甲",
    "鱼露",
    "猪油",
    "鸡油",
    "鸭油",
    "蛙",
    "鳖",
    "龟",
    "兔",
    "鸽",
    "鹌鹑",
    "鳝",
    "鳗",
)
_UNKNOWN = (
    "鸡精",
    "鸡粉",
    "高汤",
    "浓汤",
    "汤料",
    "蘸料",
    "酱料",
    "调味料",
    "料包",
    "咖喱块",
    "复合",
    "素肉",
    "植物肉",
    "植物鸡",
    "素鸡",
    "素鸭",
    "人造肉",
    "素香肠",
    "植物香肠",
    "人造香肠",
    "素排骨",
    "神秘",
    "未知",
)
_NO_MEAT_FOODS = (
    # Complete names, not fragments to subtract from a compound ingredient.
    "娃娃菜",
    "西芹",
    "百合",
    "金针菇",
    "白糖",
    "白砂糖",
    "红糖",
    "葱花",
    "大葱白",
    "葱白",
    "葱姜",
    "黄豆芽",
    "绿豆芽",
    "桂圆干",
    "白菜",
    "青菜",
    "菠菜",
    "西兰花",
    "花菜",
    "菜花",
    "韭菜",
    "芹菜",
    "生菜",
    "油菜",
    "油麦菜",
    "空心菜",
    "茄子",
    "番茄",
    "西红柿",
    "黄瓜",
    "冬瓜",
    "南瓜",
    "贝贝南瓜",
    "蔬菜粒",
    "丝瓜",
    "苦瓜",
    "胡萝卜",
    "白萝卜",
    "萝卜",
    "莴笋",
    "芦笋",
    "莲藕",
    "藕",
    "竹笋",
    "土豆",
    "山药",
    "芋头",
    "红薯",
    "玉米",
    "蚕豆",
    "豌豆",
    "黄豆",
    "毛豆",
    "红豆",
    "绿豆",
    "豆腐",
    "豆干",
    "腐竹",
    "豆皮",
    "香干",
    "豆芽",
    "百叶",
    "千张",
    "菇",
    "鸡腿菇",
    "蟹味菇",
    "牛肝菌",
    "羊肚菌",
    "鱼腥草",
    "木耳",
    "银耳",
    "菌",
    "香菇",
    "海带",
    "紫菜",
    "马蹄",
    "荸荠",
    "鸡头米",
    "鸡蛋",
    "鸭蛋",
    "鹅蛋",
    "鹌鹑蛋",
    "蛋清",
    "蛋黄",
    "全蛋",
    "牛奶",
    "羊奶",
    "奶粉",
    "淡奶油",
    "奶油",
    "炼乳",
    "蛋液",
    "蛋白",
    "蛋",
    "奶酪",
    "芝士",
    "黄油",
    "酸奶",
    "大米",
    "糯米",
    "粳米",
    "米饭",
    "熟米饭",
    "面粉",
    "淀粉",
    "豌豆淀粉",
    "挂面",
    "面条",
    "面团",
    "苹果",
    "梨",
    "柠檬",
    "果肉",
    "荔枝",
    "桂圆",
    "龙眼",
    "红枣",
    "枸杞",
    "葱",
    "姜",
    "蒜",
    "香菜",
    "食用油",
    "植物油",
    "菜籽油",
    "橄榄油",
    "香油",
    "芝麻油",
    "豆油",
    "玉米油",
    "花生油",
    "水",
    "盐",
    "糖",
    "蜂蜜",
    "醋",
    "米醋",
    "料酒",
    "生抽",
    "老抽",
    "酱油",
    "胡椒",
    "黑胡椒",
    "八角",
    "桂皮",
    "肉桂",
    "香叶",
    "花椒",
    "辣椒",
    "芝麻",
    "花生",
    "核桃",
    "杏仁",
    "酵母",
    "味精",
)

# Only finite preparation descriptors at the edges of a whole known food.
# Do not delete arbitrary internal substrings: 金针菇 != 金针 + 菇, and
# 香菇复合酱 is not established merely because it contains 香菇.
_KNOWN_NO_MEAT_NAME = re.compile(
    r"(?:新鲜|鲜|嫩|干|老|小|大|冷|冰|热|去皮|去籽|切碎|切片|切丝|切块)*"
    + "(?:"
    + "|".join(re.escape(term) for term in sorted(_NO_MEAT_FOODS, key=len, reverse=True))
    + ")"
    + r"(?:碎|叶|片|丝|末|粒|段)*"
)
_MEAT_ANALOGUE_SAUSAGE = re.compile(r"(?:素|植物|人造)(?:鸡肉|鸡|鸭|肉|排骨)?(?:香肠|腊肠)")


def known_no_meat_food(food: str) -> bool:
    """Require the whole declared name to be covered, not one substring."""
    text = re.sub(r"\s+", "", food)
    return _KNOWN_NO_MEAT_NAME.fullmatch(text) is not None


def dish_kind(recipe: Recipe, constraints: Constraints | None = None) -> DishKind:
    """Classify non-soup, non-staple entrées using actual source ingredients.

    Meat/fish/broth/garnish evidence counts as meat, even in a vegetable-role
    dish. No-meat counts permit eggs/dairy, but require finite positive evidence
    for all declared ingredients and no animal evidence in steps. Unknown
    compound seasonings are not certified no-meat; this is not brand, vegan,
    allergy or cross-contact verification. Soups and staples never fill quotas.
    An explicitly grounded independent_entree scope additionally requires the
    finite named meat-body reference; unsupported animal dishes become other,
    never vegetarian. Legacy/default callers retain the animal-source meaning.
    """
    if "soup" in recipe.categories:
        return "soup"
    if "staple" in recipe.categories:
        return "other"
    kind = non_meat_source_kind(recipe)
    if kind == "meat" and constraints is not None and constraints.meat_dish_scope == "independent_entree":
        from app.domain.entree_preferences import independent_meat_entree_reference

        # Missing body evidence does not make an animal-containing dish vegetarian.
        # The stricter finite named-body reference is never a serving claim.
        if not independent_meat_entree_reference(recipe):
            return "other"
    return kind


def non_meat_source_kind(recipe: Recipe) -> SourceKind:
    """Check source ingredients/steps irrespective of soup or staple role.

    Declared eggs/dairy are permitted; unknown ingredients, uncertain steps
    and incomplete parsing cannot become positive no-meat evidence. This is
    finite recipe-text screening, not brand or cross-contact certification.
    """
    if "unparsed_ingredients" in recipe.quality_flags:
        return "other"
    foods = [i.name for i in recipe.ingredients]
    source_text = "、".join(foods) + "；" + recipe.steps
    text = without_plant_animal_homonyms(source_text).replace("羊肚菌", "")
    # A declared plant analogue with unknown formulation is not literal meat.
    # Mask the entire analogue, never just 植物鸡肉 leaving 香肠 behind.
    has_analogue_sausage = _MEAT_ANALOGUE_SAUSAGE.search(text) is not None
    text = _MEAT_ANALOGUE_SAUSAGE.sub("", text)
    # Eggs and dairy do not count as meat in the declared ovo-lacto convention.
    for term in (
        "鸡蛋",
        "鸭蛋",
        "牛奶",
        "羊奶",
        "鹅蛋",
        "鹌鹑蛋",
        "鸡精",
        "鸡粉",
        "素鸡",
        "素鸭",
        "素肉",
        "植物鸡肉",
        "植物肉",
        "素排骨",
        "鱼香",
        "蒸鱼豉油",
    ):
        text = text.replace(term, "")
    if any(term in text for term in _ANIMAL) or any(
        declared_protein_foods(food) & ANIMAL_PROTEIN_FOODS for food in foods
    ):
        return "meat"
    if not foods or has_analogue_sausage or any(term in source_text for term in _UNKNOWN):
        return "other"
    if all(known_no_meat_food(food) for food in foods):
        return "vegetarian"
    return "other"


def composition_active(constraints: Constraints) -> bool:
    """Whether the user has supplied at least one exact entrée quota."""
    return any(value is not None for value in (
        constraints.meat_dish_count, constraints.vegetarian_dish_count,
        constraints.meat_soup_count, constraints.vegetarian_soup_count,
    ))


def soup_source_counts(menu: Sequence[Recipe]) -> Counter[str]:
    """Count finite whole-source soup kinds; unknown is not certified no-meat."""
    return Counter(non_meat_source_kind(r) for r in menu if "soup" in r.categories)


def composition_counts(menu: Sequence[Recipe], constraints: Constraints | None = None) -> Counter[str]:
    """Count finite source-backed culinary kinds, not protein or portions."""
    return Counter(dish_kind(recipe, constraints) for recipe in menu)


def composition_satisfied(menu: Sequence[Recipe], constraints: Constraints, *,
    previous: Sequence[Recipe] | None = None) -> bool:
    """Check exact quotas; swaps also retain already-covered mixed references.

    Final validation without previous still checks only exact user quotas.
    Missing qualitative references remain disclosed, not invented hard quotas.
    """
    if previous is not None:
        from app.domain.entree_preferences import mixed_entree_preserves

        if not mixed_entree_preserves(previous, menu, constraints):
            return False
    if not composition_active(constraints):
        return True
    counts = composition_counts(menu, constraints)
    soups = soup_source_counts(menu)
    return all(
        target is None or counts[kind] == target
        for kind, target in (
            ("meat", constraints.meat_dish_count),
            ("vegetarian", constraints.vegetarian_dish_count),
        )
    ) and all(target is None or soups[kind] == target for kind, target in (
        ("meat", constraints.meat_soup_count),
        ("vegetarian", constraints.vegetarian_soup_count),
    ))


def composition_issue(constraints: Constraints) -> str | None:
    """Reject incompatible explicit counts rather than silently changing totals."""
    requested = (constraints.meat_dish_count or 0) + (constraints.vegetarian_dish_count or 0)
    requested_soups = (constraints.meat_soup_count or 0) + (constraints.vegetarian_soup_count or 0)
    if requested_soups > constraints.soup_count:
        return "荤汤与素汤数量超过总汤数，请确认总汤数及各来源数量；汤不计入非汤荤素菜数。"
    if requested > constraints.dish_count - constraints.soup_count:
        return "荤菜与素菜数量超过非汤菜位，请确认总菜数、汤数和荤素数量；汤与主食不计入荤素菜数。"
    return None
